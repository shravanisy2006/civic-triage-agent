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


def priority_score(analysis: IssueAnalysis, duplicate_count: int = 0) -> int:
    """0-100. Severity dominates; hazards and repeated reports push an issue up."""
    score = analysis.severity * 14
    score += 20 if analysis.hazard else 0
    score += CATEGORY_WEIGHT[analysis.category]
    score += min(duplicate_count, 5) * 4  # community signal, capped so it can't be spammed
    return max(0, min(100, score))


def priority_label(score: int) -> str:
    if score >= 75:
        return "P1 - Critical"
    if score >= 50:
        return "P2 - High"
    if score >= 30:
        return "P3 - Normal"
    return "P4 - Low"


def route(analysis: IssueAnalysis) -> str:
    # Low-confidence results go to a human instead of a possibly wrong department.
    if analysis.confidence < 0.3:
        return "Manual review"
    return DEPARTMENTS[analysis.category]


def sla_hours(label: str) -> int:
    return {"P1": 4, "P2": 24, "P3": 72, "P4": 168}[label[:2]]


def explain(analysis: IssueAnalysis, duplicate_count: int, score: int) -> str:
    parts = [f"severity {analysis.severity}/5 (+{analysis.severity * 14})"]
    if analysis.hazard:
        parts.append("safety hazard (+20)")
    parts.append(f"{analysis.category.value} weight (+{CATEGORY_WEIGHT[analysis.category]})")
    if duplicate_count:
        parts.append(f"{duplicate_count} similar open report(s) (+{min(duplicate_count, 5) * 4})")
    return f"Score {score} = " + ", ".join(parts)
