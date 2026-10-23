"""Tests for the security, robustness and data-access safeguards."""
import io

import pytest
from PIL import Image

from src import config, db, rules, security
from src.schemas import Category, IssueAnalysis


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "h.db")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    db.init_db()


# ---------- File signatures ----------
def test_sniff_accepts_real_signatures():
    assert security.sniff_image_type(_png()) == "PNG"
    assert security.sniff_image_type(b"\xff\xd8\xff\xe0rest") == "JPEG"
    assert security.sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "WEBP"


@pytest.mark.parametrize("payload", [b"%PDF-1.7 ...", b"MZ\x90\x00executable", b"<svg onload=alert(1)>",
                                     b"RIFF\x00\x00\x00\x00WAVEfmt "])
def test_non_image_payloads_rejected_before_decoding(payload):
    with pytest.raises(security.ValidationError):
        security.validate_and_clean_image(payload)


def test_truncated_image_rejected():
    with pytest.raises(security.ValidationError):
        security.validate_and_clean_image(_png()[:30])


# ---------- Markdown escaping ----------
def test_md_escape_neutralises_links_images_and_formatting():
    out = security.md_escape("[click](https://evil.site) ![x](http://t/p.png) **bold** <script>")
    for raw in ("[click](", "![x](", "**bold**", "<script>"):
        assert raw not in out


def test_md_escape_handles_none_and_plain_text():
    assert security.md_escape(None) == ""
    assert security.md_escape("Pothole near gate") == "Pothole near gate"


# ---------- Login lockout ----------
def test_login_guard_locks_after_repeated_failures():
    g = security.LoginGuard(max_failures=3, lockout_s=60)
    assert not any(g.check("bad", "4321", now=t) for t in (0, 1, 2))
    assert g.seconds_locked(now=3) > 0
    assert not g.check("4321", "4321", now=3)        # correct PIN refused while locked
    assert g.check("4321", "4321", now=63)           # allowed again after lockout


def test_login_guard_rejects_empty_pin():
    assert not security.LoginGuard().check("", "4321")


# ---------- Database safeguards ----------
def test_insert_rejects_unknown_columns(fresh_db):
    with pytest.raises(ValueError):
        db.insert_report({"area": "x", "category": "other; DROP TABLE reports"  # value is fine...
                          , "evil_column) VALUES (1); --": 1})                  # ...column is not


def test_update_status_rejects_unknown_status(fresh_db):
    from src.pipeline import process_report
    r = process_report(None, "Garbage pile", "Ravet")
    with pytest.raises(ValueError):
        db.update_status(r.report_id, "Deleted")
    assert db.get_report(r.report_id)["status"] == "Open"


def test_sql_injection_text_is_stored_literally(fresh_db):
    from src.pipeline import process_report
    r = process_report(None, "x'); DROP TABLE reports; --", "Ravet")
    assert db.get_report(r.report_id)["description"] == "x'); DROP TABLE reports; --"
    assert db.report_counts()["total"] == 1


def test_report_counts_and_list_without_images(fresh_db):
    from src.pipeline import process_report
    a = process_report(_png(), "Live wire sparking near school", "Akurdi")
    process_report(None, "Garbage pile", "Ravet")
    db.update_status(a.report_id, "Resolved")
    assert db.report_counts() == {"total": 2, "unresolved": 1, "resolved": 1, "critical_open": 0}
    rows = db.list_reports("All", with_image=False)
    assert rows and all("image" not in r for r in rows)
    assert any(r["image"] for r in db.list_reports("All"))


def test_indexes_exist(fresh_db):
    with db.connect() as c:
        names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_dup", "idx_queue", "idx_image_hash", "idx_duplicate_of"} <= names


# ---------- Rules edge cases ----------
def _a(sev=3, hazard=False, conf=0.9, cat=Category.OTHER):
    return IssueAnalysis(category=cat, severity=sev, hazard=hazard, summary="s", confidence=conf)


def test_score_bounds_and_negative_duplicates():
    assert 0 <= rules.priority_score(_a(sev=1), -5) <= 100
    assert rules.priority_score(_a(5, True, cat=Category.STREETLIGHT), 99) <= 100


@pytest.mark.parametrize("score,label", [(75, "P1 - Critical"), (74, "P2 - High"), (50, "P2 - High"),
                                         (49, "P3 - Normal"), (30, "P3 - Normal"), (29, "P4 - Low")])
def test_priority_label_boundaries(score, label):
    assert rules.priority_label(score) == label


def test_confidence_threshold_boundary():
    assert rules.route(_a(conf=rules.LOW_CONFIDENCE)) != "Manual review"
    assert rules.route(_a(conf=rules.LOW_CONFIDENCE - 0.01)) == "Manual review"


def test_ai_output_with_missing_fields_rejected():
    with pytest.raises(Exception):
        IssueAnalysis.model_validate_json('{"category": "other", "severity": 3}')
