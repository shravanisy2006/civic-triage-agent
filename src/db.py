"""SQLite storage. Every query is parameterised (no string formatting) to prevent SQL injection."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from src import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    area TEXT NOT NULL,
    area_key TEXT NOT NULL,
    description TEXT,
    image BLOB,
    image_hash TEXT,
    category TEXT NOT NULL,
    severity INTEGER NOT NULL,
    hazard INTEGER NOT NULL,
    summary TEXT,
    confidence REAL,
    priority INTEGER NOT NULL,
    priority_label TEXT NOT NULL,
    department TEXT NOT NULL,
    duplicate_of INTEGER,
    status TEXT NOT NULL DEFAULT 'Open',
    source TEXT NOT NULL,
    flagged INTEGER NOT NULL DEFAULT 0,
    explanation TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dup ON reports(area_key, category, status);
CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key TEXT PRIMARY KEY,
    analysis_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    source TEXT NOT NULL      -- gemini | fallback | cache
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)
        # Lightweight migration for databases created by earlier versions
        cols = {r["name"] for r in c.execute("PRAGMA table_info(reports)")}
        if "is_demo" not in cols:
            c.execute("ALTER TABLE reports ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
        # keep stored area keys consistent with the current normalisation
        for row in c.execute("SELECT id, area, area_key FROM reports").fetchall():
            k = area_key(row["area"])
            if k != row["area_key"]:
                c.execute("UPDATE reports SET area_key = ? WHERE id = ?", (k, row["id"]))


def delete_demo_reports() -> int:
    with connect() as c:
        return c.execute("DELETE FROM reports WHERE is_demo = 1").rowcount


def count_demo_reports() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM reports WHERE is_demo = 1").fetchone()[0]


_AREA_STOPWORDS = {"near", "opp", "opposite", "the", "road", "rd", "lane", "no", "area"}


def area_key(area: str) -> str:
    """Order- and punctuation-insensitive locality key, so 'Sector 21, Pimpri' == 'pimpri sector-21'."""
    import re
    tokens = re.findall(r"[a-z0-9]+", area.lower())
    return " ".join(sorted(t for t in set(tokens) if t not in _AREA_STOPWORDS)) or area.lower().strip()


# ---------- Cache ----------
def cache_get(key: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT analysis_json FROM ai_cache WHERE cache_key = ?", (key,)).fetchone()
    return json.loads(row["analysis_json"]) if row else None


def cache_put(key: str, analysis: dict):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO ai_cache (cache_key, analysis_json, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(analysis), _now()),
        )


# ---------- Reports ----------
def find_duplicates(area: str, category: str) -> list[sqlite3.Row]:
    since = (datetime.now(timezone.utc) - timedelta(days=config.DUPLICATE_WINDOW_DAYS)).isoformat()
    with connect() as c:
        return c.execute(
            """SELECT id, priority FROM reports
               WHERE area_key = ? AND category = ? AND status != 'Resolved' AND created_at >= ?
               ORDER BY id""",
            (area_key(area), category, since),
        ).fetchall()


def find_same_image(image_hash: str) -> int | None:
    with connect() as c:
        row = c.execute("SELECT id FROM reports WHERE image_hash = ? ORDER BY id LIMIT 1", (image_hash,)).fetchone()
    return row["id"] if row else None


def get_report(report_id: int) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def report_history(report_id: int) -> list[dict]:
    """Audit entries about one report (creation, status changes)."""
    with connect() as c:
        rows = c.execute(
            "SELECT created_at, event, detail FROM audit_log WHERE detail = ? OR detail LIKE ? ORDER BY id",
            (f"#{report_id}", f"#{report_id} %")).fetchall()
    return [dict(r) for r in rows]


def linked_reports(report_id: int) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT id, created_at, area, status FROM reports WHERE duplicate_of = ? ORDER BY id",
                         (report_id,)).fetchall()
    return [dict(r) for r in rows]


def insert_report(r: dict) -> int:
    cols = list(r.keys()) + ["created_at", "area_key"]
    vals = list(r.values()) + [_now(), area_key(r["area"])]
    placeholders = ", ".join("?" for _ in cols)
    with connect() as c:
        cur = c.execute(f"INSERT INTO reports ({', '.join(cols)}) VALUES ({placeholders})", vals)
        return cur.lastrowid


def bump_priority(report_id: int, new_priority: int, new_label: str, by_report: int | None = None):
    """Raise an existing report's priority when a duplicate arrives, and record why."""
    note = f" · raised to {new_priority} by duplicate report" + (f" #{by_report}" if by_report else "")
    with connect() as c:
        c.execute(
            """UPDATE reports SET
                 explanation = CASE WHEN ? > priority THEN explanation || ? ELSE explanation END,
                 priority_label = CASE WHEN ? > priority THEN ? ELSE priority_label END,
                 priority = MAX(priority, ?)
               WHERE id = ?""",
            (new_priority, note, new_priority, new_label, new_priority, report_id),
        )


def list_reports(status: str | None = None, department: str | None = None) -> list[dict]:
    q = "SELECT * FROM reports WHERE 1=1"
    params: list = []
    if status and status != "All":
        q += " AND status = ?"
        params.append(status)
    if department and department != "All":
        q += " AND department = ?"
        params.append(department)
    q += " ORDER BY (status = 'Resolved'), priority DESC, id ASC"
    with connect() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def update_status(report_id: int, status: str):
    with connect() as c:
        c.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))


# ---------- Metrics & audit ----------
def log_metric(latency_ms: float, source: str):
    with connect() as c:
        c.execute("INSERT INTO metrics (created_at, latency_ms, source) VALUES (?, ?, ?)", (_now(), latency_ms, source))


def audit(event: str, detail: str = ""):
    with connect() as c:
        c.execute("INSERT INTO audit_log (created_at, event, detail) VALUES (?, ?, ?)", (_now(), event, detail[:300]))


def metrics_summary() -> dict:
    with connect() as c:
        rows = c.execute("SELECT source, COUNT(*) n, AVG(latency_ms) avg_ms FROM metrics GROUP BY source").fetchall()
        recent = [dict(r) for r in c.execute(
            "SELECT created_at, latency_ms, source FROM metrics ORDER BY id DESC LIMIT 50").fetchall()]
        audit_rows = [dict(r) for r in c.execute(
            "SELECT created_at, event, detail FROM audit_log ORDER BY id DESC LIMIT 20").fetchall()]
        by_cat = [dict(r) for r in c.execute(
            "SELECT category, COUNT(*) n FROM reports GROUP BY category").fetchall()]
    return {"by_source": {r["source"]: {"n": r["n"], "avg_ms": r["avg_ms"]} for r in rows},
            "recent": recent, "audit": audit_rows, "by_category": by_cat}
