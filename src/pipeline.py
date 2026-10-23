"""The agent pipeline: validate -> sanitise -> cache -> analyse (Gemini) -> score -> dedup -> route -> store.
Each step is a separate, testable function; the UI only calls process_report()."""
import hashlib
import time
from dataclasses import dataclass

from src import ai, db, rules, security
from src.config import MAX_TEXT_CHARS
from src.schemas import IssueAnalysis


@dataclass
class TriageResult:
    report_id: int
    analysis: IssueAnalysis
    source: str
    latency_ms: float
    priority: int
    label: str
    department: str
    duplicate_of: int | None
    duplicate_count: int
    explanation: str
    flagged: bool
    steps: list[str]
    fallback_reason: str = ""          # why Gemini was not used (empty when it was)
    total_ms: float = 0.0              # end-to-end scan duration
    same_photo_of: int | None = None   # earlier report that used the identical photo


def process_report(image_bytes: bytes | None, description: str, area: str,
                   is_demo: bool = False) -> TriageResult:
    """is_demo marks reports whose INPUT is sample data; their analysis is still computed for real."""
    steps = []
    t0 = time.perf_counter()

    # 1. Validate and sanitise all input (raises security.ValidationError on bad input)
    area = security.normalise_area(area)
    description = security.clean_text(description, MAX_TEXT_CHARS)
    clean_img, img_hash = (None, None)
    if image_bytes:
        clean_img, img_hash = security.validate_and_clean_image(image_bytes)
        steps.append("Image validated, resized and metadata (EXIF/GPS) removed")
    if not clean_img and not description:
        raise security.ValidationError("Please add a photo or a description.")

    redacted = security.redact_pii(description)
    if redacted != description:
        steps.append("Personal details redacted from description")
    flagged = security.looks_like_injection(redacted)
    if flagged:
        steps.append("Possible prompt-injection detected - flagged for review")
        db.audit("injection_flag", redacted)

    same_photo = db.find_same_image(img_hash) if img_hash else None
    if same_photo:
        steps.append(f"Identical photo already submitted in report #{same_photo}")

    # 2. Cache lookup (same image + same text => no API call)
    cache_key = hashlib.sha256(f"{img_hash}|{redacted.lower()}".encode()).hexdigest()
    cached = db.cache_get(cache_key)
    fallback_reason = ""
    if cached:
        analysis, source = IssueAnalysis.model_validate(cached), "cache"
        latency = (time.perf_counter() - t0) * 1000
        steps.append("Cache hit - reused earlier AI analysis (0 API calls)")
    else:
        analysis, source, latency = ai.analyze(clean_img, redacted)
        fallback_reason = ai.last_error if source == "fallback" else ""
        steps.append("Analysed by Gemini (multimodal, schema-validated)" if source == "gemini"
                     else f"Gemini unavailable ({ai.last_error}) - rule-based fallback used")
        if source == "gemini":
            db.cache_put(cache_key, analysis.model_dump(mode="json"))
    db.log_metric(latency, source)

    # 3. Decisions in deterministic code
    if flagged:  # never let a manipulated report jump the queue
        analysis = analysis.model_copy(update={"confidence": min(analysis.confidence, 0.29)})
    dups = db.find_duplicates(area, analysis.category.value)
    score = rules.priority_score(analysis, len(dups))
    label = rules.priority_label(score)
    department = rules.route(analysis)
    duplicate_of = dups[0]["id"] if dups else None
    explanation = rules.explain(analysis, len(dups), score)
    steps.append(f"Priority {label} ({score}/100), routed to {department}")
    if duplicate_of:
        steps.append(f"Linked to existing report #{duplicate_of} ({len(dups)} similar open)")

    # 4. Store
    report_id = db.insert_report({
        "area": area, "description": redacted, "image": clean_img, "image_hash": img_hash,
        "category": analysis.category.value, "severity": analysis.severity,
        "hazard": int(analysis.hazard), "summary": analysis.summary, "confidence": analysis.confidence,
        "priority": score, "priority_label": label, "department": department,
        "duplicate_of": duplicate_of, "source": source, "flagged": int(flagged), "explanation": explanation,
        "is_demo": int(is_demo),
    })
    db.audit("report_created", f"#{report_id} {label} {department}")
    if duplicate_of:
        db.bump_priority(duplicate_of, score, label, by_report=report_id)

    return TriageResult(report_id, analysis, source, latency, score, label, department,
                        duplicate_of, len(dups), explanation, flagged, steps,
                        fallback_reason, (time.perf_counter() - t0) * 1000, same_photo)
