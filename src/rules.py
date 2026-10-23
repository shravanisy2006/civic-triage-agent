"""Deterministic decision engine. The AI describes; these rules decide.
Keeping decisions in plain code makes them explainable, testable and tamper-proof."""
from src.schemas import DEPARTMENTS, Category, IssueAnalysis

CATEGORY_WEIGHT = {
    Category.STREETLIGHT: 10,  # electrical risk
    Category.WATER: 8,
    Category.TREE: 8,
    Category.POTHOLE: 6,
    Category.GARBAGE: 4,
    Category.OTHER: 0,
}


# Scoring weights - kept as named constants so the formula is easy to audit and tune.
SEVERITY_POINTS = 14          # per severity level (1-5)
HAZARD_POINTS = 20
DUPLICATE_POINTS = 4          # per similar open report...
MAX_COUNTED_DUPLICATES = 5    # ...capped so repeated reports can't be used to spam the queue
LOW_CONFIDENCE = 0.3          # below this, a human reviews the routing
SLA_HOURS = {"P1": 4, "P2": 24, "P3": 72, "P4": 168}


def priority_score(analysis: IssueAnalysis, duplicate_count: int = 0) -> int:
    """Deterministic 0-100 score: severity dominates; hazards, risky categories and
    repeated reports push an issue up. The same input always gives the same score."""
    score = analysis.severity * SEVERITY_POINTS
    score += HAZARD_POINTS if analysis.hazard else 0
    score += CATEGORY_WEIGHT[analysis.category]
    score += min(max(duplicate_count, 0), MAX_COUNTED_DUPLICATES) * DUPLICATE_POINTS
    return max(0, min(100, score))


def priority_label(score: int) -> str:
    """Map a 0-100 score to a P1 (critical) ... P4 (low) label."""
    if score >= 75:
        return "P1 - Critical"
    if score >= 50:
        return "P2 - High"
    if score >= 30:
        return "P3 - Normal"
    return "P4 - Low"


def route(analysis: IssueAnalysis) -> str:
    """Department for the issue; low-confidence results go to a human instead of a
    possibly wrong department."""
    if analysis.confidence < LOW_CONFIDENCE:
        return "Manual review"
    return DEPARTMENTS[analysis.category]


def sla_hours(label: str) -> int:
    """Target response time in hours for a priority label such as 'P1 - Critical'."""
    return SLA_HOURS[label[:2]]


def explain(analysis: IssueAnalysis, duplicate_count: int, score: int) -> str:
    """Human-readable breakdown of how the score was built."""
    parts = [f"severity {analysis.severity}/5 (+{analysis.severity * SEVERITY_POINTS})"]
    if analysis.hazard:
        parts.append(f"safety hazard (+{HAZARD_POINTS})")
    parts.append(f"{analysis.category.value} weight (+{CATEGORY_WEIGHT[analysis.category]})")
    if duplicate_count:
        parts.append(f"{duplicate_count} similar open report(s) "
                     f"(+{min(duplicate_count, MAX_COUNTED_DUPLICATES) * DUPLICATE_POINTS})")
    return f"Score {score} = " + ", ".join(parts)
