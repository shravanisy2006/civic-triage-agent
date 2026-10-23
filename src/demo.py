"""Sample reports so the dashboard can be demonstrated without real citizens.
The INPUTS below are made up (and marked is_demo=1 in the database, shown with a SAMPLE badge).
The analysis, scoring, routing and duplicate detection run through the real pipeline."""
from src.pipeline import process_report

SAMPLE_REPORTS = [
    ("Live electric wire hanging low from pole near the school gate, sparks at night", "Pimpri, Sector 21"),
    ("Huge pothole in the middle of the main road, two bikers fell this morning", "Akurdi Station Road"),
    ("Garbage not collected for 5 days, pile overflowing near the vegetable market", "Chinchwad Gaon"),
    ("Garbage dump growing near market, stray animals spreading it on the road", "chinchwad gaon"),
    ("Manhole cover missing on the footpath outside the primary school", "Nigdi Pradhikaran"),
    ("Streetlight on lane 4 not working for a week, lane is completely dark", "Ravet"),
]


def load_samples() -> int:
    for text, area in SAMPLE_REPORTS:
        process_report(None, text, area, is_demo=True)
    return len(SAMPLE_REPORTS)
