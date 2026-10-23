"""Civic Issue Triage Agent - Streamlit UI.
Run with:  streamlit run app.py"""
import hmac
from html import escape

import pandas as pd
import streamlit as st

from src import ai, config, db, demo
from src.pipeline import process_report
from src.schemas import DEPARTMENTS, STATUSES
from src.security import RateLimiter, ValidationError

st.set_page_config(page_title="Civic Triage Agent", page_icon="🛠️", layout="wide")
db.init_db()

st.session_state.setdefault("limiter", RateLimiter())
st.session_state.setdefault("officer_ok", False)
st.session_state.setdefault("last_result", None)

PRIORITY_STYLE = {"P1": ("#b91c1c", "#fee2e2"), "P2": ("#c2410c", "#ffedd5"),
                  "P3": ("#a16207", "#fef9c3"), "P4": ("#15803d", "#dcfce7")}

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1200px;}
.hero h1 {font-size: 1.9rem; margin: 0 0 .2rem 0;}
.hero p {color: #475569; margin: 0 0 1rem 0;}
.badge {display:inline-block; padding:.15rem .55rem; border-radius:999px; font-size:.78rem;
        font-weight:600; margin-right:.35rem; border:1px solid transparent; white-space:nowrap;}
.b-ai {background:#dbeafe; color:#1e40af;}
.b-rule {background:#f1f5f9; color:#334155; border-color:#cbd5e1;}
.b-cache {background:#ede9fe; color:#5b21b6;}
.b-demo {background:#fef3c7; color:#92400e; border-color:#f59e0b;}
.b-flag {background:#fee2e2; color:#991b1b;}
.card {background:#fff; border:1px solid #e2e8f0; border-radius:12px; padding:1rem 1.2rem; margin-bottom:.8rem;}
.card h3 {margin:.1rem 0 .4rem 0; font-size:1.15rem;}
.muted {color:#64748b; font-size:.85rem;}
[data-testid="stMetricValue"] {font-size:1.35rem;}
@media (max-width: 640px) { .hero h1 {font-size:1.45rem;} }
</style>
""", unsafe_allow_html=True)


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{escape(text)}</span>'


def priority_badge(label: str) -> str:
    fg, bg = PRIORITY_STYLE[label[:2]]
    return f'<span class="badge" style="background:{bg};color:{fg}">{escape(label)}</span>'


def source_badge(source: str) -> str:
    return {"gemini": badge("AI analysis · Gemini", "b-ai"),
            "cache": badge("AI analysis · cached", "b-cache"),
            "fallback": badge("Rule-based estimate", "b-rule")}[source]


def nice(cat: str) -> str:
    return cat.replace("_", " ").title()


# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown("### 🛠️ Civic Triage Agent")
    page = st.radio("Navigate", ["Report an issue", "Officer dashboard", "System metrics"],
                    label_visibility="collapsed")
    st.divider()
    if ai.gemini_available():
        st.success(f"Gemini key configured\n\nModel: `{config.GEMINI_MODEL}`")
    else:
        st.warning("No GEMINI_API_KEY found. Running in **rule-based mode** - results are estimates.")
    st.caption("Results labelled **AI analysis** come from Gemini. **Rule-based estimate** means the "
               "keyword fallback was used. **SAMPLE** marks demo inputs.")


# ---------------- Citizen page ----------------
def citizen_page():
    st.markdown('<div class="hero"><h1>Report a civic issue</h1>'
                '<p>Add a photo and a short description. The agent classifies the problem, rates its '
                'severity, checks for duplicates and routes it to the right department.</p></div>',
                unsafe_allow_html=True)

    form_col, result_col = st.columns([1, 1], gap="large")
    with form_col:
        with st.form("report", clear_on_submit=False, border=True):
            photo = st.file_uploader("Photo of the problem", type=["jpg", "jpeg", "png", "webp"],
                                     help="JPG, PNG or WEBP up to 5 MB. Location metadata is removed automatically.")
            description = st.text_area("What is the problem?", max_chars=config.MAX_TEXT_CHARS, height=110,
                                       placeholder="e.g. Deep pothole near the bus stop, two bikes slipped today")
            area = st.text_input("Area / locality *", max_chars=config.MAX_AREA_CHARS,
                                 placeholder="e.g. Pimpri, Sector 21")
            st.caption("Add a photo, a description, or both. Please don't include phone numbers - "
                       "they are removed for your privacy.")
            submitted = st.form_submit_button("Submit report", type="primary", width="stretch")
        if photo:
            st.image(photo, caption="Preview", width=260)

    if submitted:
        if not st.session_state.limiter.allow():
            st.session_state.last_result = ("error", "Too many reports from this session. "
                                                     "Please wait a minute and try again.")
            db.audit("rate_limited")
        else:
            try:
                with result_col, st.spinner("Agent is analysing the report..."):
                    st.session_state.last_result = (
                        "ok", process_report(photo.getvalue() if photo else None, description, area))
            except ValidationError as e:
                st.session_state.last_result = ("error", str(e))
                db.audit("rejected_input", str(e))
            except Exception:
                st.session_state.last_result = ("error", "Something went wrong while processing the "
                                                         "report. Please try again.")
                db.audit("internal_error")

    with result_col:
        res = st.session_state.last_result
        if res is None:
            st.info("**No report submitted yet.** Your triage result will appear here.")
            return
        kind, r = res
        if kind == "error":
            st.error(r)
            return
        render_result(r)


def render_result(r):
    a = r.analysis
    badges = priority_badge(r.label) + source_badge(r.source)
    if a.hazard:
        badges += badge("Safety hazard", "b-flag")
    st.markdown(f'<div class="card"><div class="muted">Report #{r.report_id} registered</div>'
                f'<h3>{escape(nice(a.category.value))}</h3>{badges}'
                f'<p style="margin:.6rem 0 0 0">{escape(a.summary)}</p></div>', unsafe_allow_html=True)
    if r.source == "fallback":
        st.warning("**Gemini was not used - this is a rule-based keyword estimate, not an AI result.** "
                   f"Reason: {r.fallback_reason or 'unavailable'}.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Severity", f"{a.severity} / 5")
    c2.metric("Hazard", "Yes" if a.hazard else "No")
    c3.metric("AI confidence", f"{a.confidence:.0%}" if r.source != "fallback" else "n/a",
              help="Gemini's self-reported confidence in the category. Not available for rule-based estimates.")
    c4, c5, c6 = st.columns(3)
    c4.metric("Priority score", f"{r.priority} / 100")
    c5.metric("Response target", f"{rules_sla(r.label)} h")
    c6.metric("Scan duration", f"{r.total_ms / 1000:.1f} s" if r.total_ms >= 100 else f"{r.total_ms:.0f} ms",
              help="End-to-end time: validation, AI analysis (or cache), scoring and storage")
    st.write(f"**Recommended department:** {r.department}")
    st.caption(f"Why this priority: {r.explanation}")
    if r.same_photo_of:
        st.info(f"This exact photo was already submitted in report #{r.same_photo_of}.")
    if r.duplicate_of:
        st.info(f"Possible duplicate: {r.duplicate_count} open report(s) of the same type in this area "
                f"(first: #{r.duplicate_of}). Your report was linked to it and raised its priority.")
    if r.flagged:
        st.warning("Your description contained unusual instructions, so it was sent for manual review.")
    with st.expander("How the agent decided"):
        for i, s in enumerate(r.steps, 1):
            st.write(f"{i}. {s}")
        st.caption(f"Analysis source: {r.source} · AI step {r.latency_ms:.0f} ms · total {r.total_ms:.0f} ms")


def rules_sla(label: str) -> int:
    from src.rules import sla_hours
    return sla_hours(label)


# ---------------- Officer page ----------------
def officer_page():
    st.markdown('<div class="hero"><h1>Officer dashboard</h1>'
                '<p>Prioritised queue of citizen reports. Highest risk first.</p></div>',
                unsafe_allow_html=True)
    if not st.session_state.officer_ok:
        with st.form("login", border=True):
            pin = st.text_input("Officer PIN", type="password")
            ok = st.form_submit_button("Unlock dashboard", type="primary")
        if ok:
            if pin and hmac.compare_digest(pin.encode(), config.OFFICER_PIN.encode()):
                st.session_state.officer_ok = True
                db.audit("officer_login")
                st.rerun()
            db.audit("officer_login_failed")
            st.error("Incorrect PIN.")
        st.caption("The PIN is set by OFFICER_PIN in your .env file.")
        return

    open_all = db.list_reports("Open") + db.list_reports("In progress")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Active reports", len(open_all))
    k2.metric("Critical (P1)", sum(r["priority_label"].startswith("P1") for r in open_all))
    k3.metric("Safety hazards", sum(r["hazard"] for r in open_all))
    k4.metric("Manual review", sum(r["department"] == "Manual review" for r in open_all))

    f1, f2, f3, f4 = st.columns([1, 2, 1, 1])
    status = f1.selectbox("Status", ["All"] + STATUSES, index=1)
    dept = f2.selectbox("Department", ["All", "Manual review"] + list(DEPARTMENTS.values()))
    n_demo = db.count_demo_reports()
    with f3:
        st.write("")
        if n_demo:
            if st.button("Remove sample data", width="stretch"):
                db.delete_demo_reports()
                db.audit("demo_cleared")
                st.rerun()
        elif st.button("Load sample data", width="stretch",
                       help="Adds 6 made-up reports (marked SAMPLE) and analyses them for real"):
            with st.spinner("Analysing 6 sample reports..."):
                demo.load_samples()
            db.audit("demo_loaded")
            st.rerun()
    with f4:
        st.write("")
        if st.button("Lock", width="stretch"):
            st.session_state.officer_ok = False
            st.rerun()

    reports = db.list_reports(status, dept)
    tab_queue, tab_detail = st.tabs(["Queue", "Incident detail"])
    with tab_detail:
        incident_detail()
    with tab_queue:
        if not reports:
            st.info("**No reports match these filters.** New citizen reports will appear here - "
                    "or click *Load sample data* to try the dashboard.")
            return

        st.markdown(f"**Queue · {len(reports)} report(s)**")
        for r in reports:
            prefix = "SAMPLE · " if r["is_demo"] else ""
            title = (f"{prefix}#{r['id']} · {r['priority_label']} ({r['priority']}) · "
                     f"{nice(r['category'])} · {r['area']} · {r['status']}")
            with st.expander(title, expanded=r["priority_label"].startswith("P1") and r["status"] != "Resolved"):
                badges = priority_badge(r["priority_label"]) + source_badge(r["source"])
                if r["is_demo"]:
                    badges += badge("SAMPLE input", "b-demo")
                if r["hazard"]:
                    badges += badge("Safety hazard", "b-flag")
                if r["flagged"]:
                    badges += badge("Possible prompt injection", "b-flag")
                left, right = st.columns([1, 2])
                with left:
                    if r["image"]:
                        st.image(r["image"], width="stretch")
                    else:
                        st.caption("No photo submitted")
                with right:
                    st.markdown(badges, unsafe_allow_html=True)
                    st.write(f"**Summary:** {r['summary']}")
                    st.write(f"**Citizen said:** {r['description'] or '-'}")
                    st.write(f"**Department:** {r['department']} · **Severity:** {r['severity']}/5")
                    st.caption(f"{r['explanation']} · confidence {conf_text(r)} · submitted {r['created_at']} UTC")
                    if r["duplicate_of"]:
                        st.caption(f"Linked as duplicate of #{r['duplicate_of']}")
                    new_status = st.selectbox("Update status", STATUSES, index=STATUSES.index(r["status"]),
                                              key=f"s{r['id']}")
                    if new_status != r["status"]:
                        db.update_status(r["id"], new_status)
                        db.audit("status_change", f"#{r['id']} -> {new_status}")
                        st.rerun()


def conf_text(r: dict) -> str:
    return "n/a (rule-based)" if r["source"] == "fallback" else f"{r['confidence']:.0%}"


def incident_detail():
    all_reports = db.list_reports("All")
    if not all_reports:
        st.info("**No incidents stored yet.**")
        return
    ids = sorted(r["id"] for r in all_reports)
    rid = st.selectbox("Select incident", ids, index=len(ids) - 1, format_func=lambda i: f"#{i}")
    r = db.get_report(rid)
    badges = priority_badge(r["priority_label"]) + source_badge(r["source"])
    if r["is_demo"]:
        badges += badge("SAMPLE input", "b-demo")
    if r["hazard"]:
        badges += badge("Safety hazard", "b-flag")
    if r["flagged"]:
        badges += badge("Possible prompt injection", "b-flag")
    st.markdown(f'<div class="card"><div class="muted">Incident #{r["id"]} · {escape(r["area"])} · '
                f'submitted {escape(r["created_at"])} UTC</div><h3>{escape(nice(r["category"]))}</h3>{badges}'
                f'<p style="margin:.6rem 0 0 0">{escape(r["summary"] or "")}</p></div>', unsafe_allow_html=True)
    left, right = st.columns([1, 2])
    with left:
        if r["image"]:
            st.image(r["image"], width="stretch", caption="Photo (metadata removed)")
        else:
            st.caption("No photo submitted")
    with right:
        c1, c2, c3 = st.columns(3)
        c1.metric("Severity", f"{r['severity']} / 5")
        c2.metric("Priority", f"{r['priority']} / 100")
        c3.metric("Status", r["status"])
        st.write(f"**Department:** {r['department']} · **Response target:** {rules_sla(r['priority_label'])} h")
        st.write(f"**Citizen said:** {r['description'] or '-'}")
        st.caption(f"{r['explanation']} · AI confidence {conf_text(r)}")
        if r["duplicate_of"]:
            st.caption(f"Duplicate of incident #{r['duplicate_of']}")
        linked = db.linked_reports(r["id"])
        if linked:
            st.write(f"**{len(linked)} linked duplicate report(s):** " +
                     ", ".join(f"#{x['id']} ({x['status']})" for x in linked))
    st.markdown("**History**")
    hist = db.report_history(r["id"])
    if hist:
        st.dataframe(pd.DataFrame(hist), width="stretch", hide_index=True)
    else:
        st.caption("No recorded events.")


# ---------------- Metrics page ----------------
def metrics_page():
    st.markdown('<div class="hero"><h1>System metrics</h1>'
                '<p>Live performance, efficiency and security figures recorded by the app.</p></div>',
                unsafe_allow_html=True)
    m = db.metrics_summary()
    src = m["by_source"]
    total = sum(v["n"] for v in src.values())
    if not total:
        st.info("**No analyses recorded yet.** Submit a report or load sample data to see metrics.")
        return

    gem, fb, cache = (src.get(k, {"n": 0, "avg_ms": None}) for k in ("gemini", "fallback", "cache"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total analyses", total)
    c2.metric("Avg Gemini latency", f"{gem['avg_ms']:.0f} ms" if gem["avg_ms"] else "No Gemini calls yet")
    c3.metric("Cache hit rate", f"{cache['n'] / total:.0%}", help="Repeat reports served with zero API calls")
    c4.metric("Fallback rate", f"{fb['n'] / total:.0%}", help="Answered by the rule engine instead of Gemini")

    left, right = st.columns(2)
    with left:
        st.markdown("**Latency of recent requests (ms)**")
        df = pd.DataFrame(m["recent"][::-1]).reset_index(names="request")
        st.bar_chart(df, x="request", y="latency_ms", color="source", height=260)
    with right:
        st.markdown("**Reports by category**")
        if m["by_category"]:
            st.bar_chart(pd.DataFrame(m["by_category"]), x="category", y="n", height=260)
        else:
            st.caption("No reports stored.")
    st.markdown("**Security audit log (latest 20)**")
    st.dataframe(pd.DataFrame(m["audit"]), width="stretch", hide_index=True)


{"Report an issue": citizen_page, "Officer dashboard": officer_page, "System metrics": metrics_page}[page]()
