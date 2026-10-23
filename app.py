"""Civic Issue Triage Agent - Streamlit UI.
Run with:  streamlit run app.py"""
import hmac
from html import escape

import pandas as pd
import streamlit as st

from src import ai, config, db, demo
from src.pipeline import process_report
from src.schemas import DEPARTMENTS, STATUSES, Category
from src.security import RateLimiter, ValidationError

st.set_page_config(page_title="CivicFlow AI", page_icon="🏙️", layout="wide")
db.init_db()

st.session_state.setdefault("limiter", RateLimiter())
st.session_state.setdefault("officer_ok", False)
st.session_state.setdefault("last_result", None)

# Priority colours (text, background) chosen for >= 4.5:1 contrast on the dark theme
PRIORITY_STYLE = {"P1": ("#fecaca", "#7f1d1d"), "P2": ("#fed7aa", "#7c2d12"),
                  "P3": ("#fde68a", "#713f12"), "P4": ("#bbf7d0", "#14532d")}
PRIORITY_ACCENT = {"P1": "#ef4444", "P2": "#f97316", "P3": "#eab308", "P4": "#22c55e"}

st.markdown("""
<style>
:root {--cf-surface:#131c2e; --cf-surface-2:#182338; --cf-border:#24324b; --cf-muted:#94a3b8;
       --cf-cyan:#22d3ee; --cf-cobalt:#3b82f6; --cf-mint:#34d399;}
.block-container {padding-top: 1.4rem; max-width: 1240px;}
.cf-brand {display:flex; align-items:center; gap:.55rem; font-weight:700; font-size:1.15rem; letter-spacing:.2px;}
.cf-logo {width:30px; height:30px; border-radius:8px; display:grid; place-items:center;
          background:linear-gradient(135deg,#0e7490,#1d4ed8); box-shadow:0 2px 10px rgba(34,211,238,.25);}
.cf-brand b {color:var(--cf-cyan); font-weight:700;}
.cf-hero {display:grid; grid-template-columns: 1.15fr .85fr; gap:1.5rem; align-items:center;
          background:linear-gradient(160deg,#111a2c 0%,#0d1526 60%,#0b1220 100%);
          border:1px solid var(--cf-border); border-radius:18px; padding:1.6rem 1.8rem; margin-bottom:1.2rem;}
.cf-eyebrow {color:var(--cf-cyan); font-size:.78rem; font-weight:600; letter-spacing:.12em; text-transform:uppercase;}
.cf-hero h1 {font-size:2.15rem; line-height:1.15; margin:.35rem 0 .6rem 0; color:#f1f5f9;}
.cf-hero p {color:#b6c2d4; margin:0; font-size:.98rem; max-width:560px;}
.cf-pills {margin-top:.9rem; display:flex; gap:.45rem; flex-wrap:wrap;}
.cf-pill {font-size:.76rem; color:#cbd5e1; border:1px solid var(--cf-border); background:var(--cf-surface);
          padding:.2rem .6rem; border-radius:999px;}
.cf-hero svg {width:100%; height:auto; max-height:210px;}
.cf-flow {stroke-dasharray:6 6; animation:cfdash 2.4s linear infinite;}
@keyframes cfdash {to {stroke-dashoffset:-24;}}
@media (prefers-reduced-motion: reduce) {.cf-flow {animation:none;}}
.page-title h2 {font-size:1.55rem; margin:0 0 .15rem 0;}
.page-title p {color:var(--cf-muted); margin:0 0 1rem 0;}
.badge {display:inline-block; padding:.16rem .6rem; border-radius:999px; font-size:.76rem;
        font-weight:600; margin:0 .35rem .25rem 0; border:1px solid transparent; white-space:nowrap;}
.b-ai {background:#0c3a4a; color:#a5f3fc; border-color:#155e75;}
.b-rule {background:#1e293b; color:#e2e8f0; border-color:#475569;}
.b-cache {background:#2e1065; color:#ddd6fe;}
.b-demo {background:#451a03; color:#fde68a; border-color:#b45309;}
.b-flag {background:#7f1d1d; color:#fecaca;}
.b-ok {background:#064e3b; color:#a7f3d0;}
.card {background:var(--cf-surface); border:1px solid var(--cf-border); border-radius:14px;
       padding:1rem 1.2rem; margin-bottom:.8rem; box-shadow:0 1px 0 rgba(255,255,255,.03) inset, 0 8px 24px rgba(0,0,0,.25);}
.card h3 {margin:.1rem 0 .45rem 0; font-size:1.2rem; color:#f1f5f9;}
.muted {color:var(--cf-muted); font-size:.85rem;}
.kpi {background:var(--cf-surface); border:1px solid var(--cf-border); border-radius:14px; padding:.85rem 1rem;
      border-top:3px solid var(--kpi, var(--cf-cobalt));}
.kpi .v {font-size:1.7rem; font-weight:700; color:#f8fafc; line-height:1.1;}
.kpi .l {color:var(--cf-muted); font-size:.8rem; text-transform:uppercase; letter-spacing:.06em;}
.qrow {border-left:4px solid var(--acc); padding-left:.6rem;}
[data-testid="stMetricValue"] {font-size:1.35rem;}
@media (max-width: 820px) { .cf-hero {grid-template-columns:1fr;} .cf-hero h1 {font-size:1.6rem;} }
</style>
""", unsafe_allow_html=True)

HERO_SVG = """
<svg viewBox="0 0 360 210" role="img" aria-label="Illustration of a connected city network">
  <defs>
    <linearGradient id="bA" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1e3a8a"/><stop offset="1" stop-color="#0f172a"/></linearGradient>
    <linearGradient id="bB" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#155e75"/><stop offset="1" stop-color="#0f172a"/></linearGradient>
  </defs>
  <polygon points="20,175 180,120 340,175 180,205" fill="#0f1a2e" stroke="#24324b"/>
  <g>
    <polygon points="60,160 90,150 90,85 60,95" fill="url(#bA)"/><polygon points="90,150 112,158 112,93 90,85" fill="#0c1a33"/>
    <polygon points="125,150 160,138 160,45 125,57" fill="url(#bB)"/><polygon points="160,138 186,147 186,54 160,45" fill="#0b2230"/>
    <polygon points="200,152 228,142 228,75 200,85" fill="url(#bA)"/><polygon points="228,142 250,150 250,83 228,75" fill="#0c1a33"/>
    <polygon points="262,160 292,150 292,105 262,115" fill="url(#bB)"/><polygon points="292,150 312,157 312,112 292,105" fill="#0b2230"/>
  </g>
  <g fill="none" stroke-width="1.6">
    <path class="cf-flow" d="M75 90 Q110 40 142 52" stroke="#22d3ee"/>
    <path class="cf-flow" d="M142 52 Q190 30 214 80" stroke="#3b82f6"/>
    <path class="cf-flow" d="M214 80 Q260 70 277 110" stroke="#22d3ee"/>
    <path class="cf-flow" d="M75 90 Q170 120 277 110" stroke="#34d399" opacity=".7"/>
  </g>
  <g>
    <circle cx="75" cy="90" r="5" fill="#ef4444"/><circle cx="75" cy="90" r="10" fill="none" stroke="#ef4444" opacity=".4"/>
    <circle cx="142" cy="52" r="5" fill="#22d3ee"/><circle cx="214" cy="80" r="5" fill="#f59e0b"/>
    <circle cx="277" cy="110" r="5" fill="#34d399"/>
  </g>
</svg>"""


def badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{escape(text)}</span>'


def priority_badge(label: str) -> str:
    fg, bg = PRIORITY_STYLE[label[:2]]
    return f'<span class="badge" style="background:{bg};color:{fg}">{escape(label)}</span>'


def source_badge(source: str) -> str:
    return {"gemini": badge("AI analysis · Gemini", "b-ai"),
            "cache": badge("AI analysis · cached", "b-cache"),
            "fallback": badge("Rule-based estimate", "b-rule")}[source]


def fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def nice(cat: str) -> str:
    return cat.replace("_", " ").title()


# ---------------- Sidebar ----------------
with st.sidebar:
    st.markdown('<div class="cf-brand"><span class="cf-logo" aria-hidden="true">⬢</span>'
                '<span>CivicFlow <b>AI</b></span></div>', unsafe_allow_html=True)
    st.caption("Civic incident triage")
    page = st.radio("Navigate", ["Report an issue", "Officer dashboard", "System metrics"],
                    label_visibility="collapsed")
    st.divider()
    if ai.gemini_available():
        st.success(f"Gemini key configured\n\nModel: `{config.GEMINI_MODEL}`")
    else:
        st.warning("No GEMINI_API_KEY found. Running in **rule-based mode** - results are estimates.")
    st.caption("**AI analysis** = Gemini result. **Rule-based estimate** = keyword fallback. "
               "**SAMPLE** = demo input.")


# ---------------- Citizen page ----------------
def citizen_page():
    st.markdown(f'''<section class="cf-hero" aria-label="CivicFlow AI introduction">
      <div><div class="cf-eyebrow">CivicFlow AI · Incident intelligence</div>
        <h1>Smarter Cities. Faster Response.</h1>
        <p>Photograph a pothole, garbage dump, broken streetlight or water leak. Gemini analyses the image,
        transparent rules set the priority, duplicates are linked, and the report is routed to the right
        department in seconds.</p>
        <div class="cf-pills"><span class="cf-pill">Gemini multimodal analysis</span>
        <span class="cf-pill">Deterministic prioritisation</span><span class="cf-pill">Duplicate detection</span>
        <span class="cf-pill">Privacy: GPS &amp; phone numbers removed</span></div></div>
      <div>{HERO_SVG}</div></section>''', unsafe_allow_html=True)
    st.markdown('<div class="page-title"><h2>Report a civic issue</h2></div>', unsafe_allow_html=True)

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
            st.image(photo, caption="Preview of your photo", width=280)

    if submitted:
        if not st.session_state.limiter.allow():
            st.session_state.last_result = ("error", "Too many reports from this session. "
                                                     "Please wait a minute and try again.")
            db.audit("rate_limited")
        else:
            try:
                with result_col, st.status("Analysing report...", expanded=True) as progress:
                    st.write("Validating input and removing personal data...")
                    st.write("Running AI analysis (Gemini) and applying priority rules...")
                    result = process_report(photo.getvalue() if photo else None, description, area)
                    progress.update(label=f"Report #{result.report_id} analysed in {fmt_ms(result.total_ms)}",
                                    state="complete", expanded=False)
                    st.session_state.last_result = ("ok", result)
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
    c6.metric("Scan duration", fmt_ms(r.total_ms),
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
    st.markdown('<div class="page-title"><h2>Operations dashboard</h2>'
                '<p>Prioritised incident queue built from stored citizen reports. Highest risk first.</p></div>',
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
        st.caption("The PIN is set by OFFICER_PIN in .env (or Streamlit secrets when deployed).")
        if config.USING_DEFAULT_PIN:
            st.warning("The default demo PIN is in use. Set OFFICER_PIN before deploying.")
        return

    everything = db.list_reports("All")
    unresolved = [r for r in everything if r["status"] != "Resolved"]
    kpis = [("Total reports", len(everything), "#3b82f6"),
            ("Critical open (P1)", sum(r["priority_label"].startswith("P1") for r in unresolved), "#ef4444"),
            ("Unresolved", len(unresolved), "#f59e0b"),
            ("Resolved", len(everything) - len(unresolved), "#34d399")]
    for col, (label, value, colour) in zip(st.columns(4), kpis):
        col.markdown(f'<div class="kpi" style="--kpi:{colour}"><div class="l">{label}</div>'
                     f'<div class="v">{value}</div></div>', unsafe_allow_html=True)
    st.write("")

    query = st.text_input("Search incidents", placeholder="Search by area, summary or description",
                          label_visibility="visible")
    f1, f2, f3, f4 = st.columns(4)
    status = f1.selectbox("Status", ["All"] + STATUSES, index=1)
    category = f2.selectbox("Category", ["All"] + [c.value for c in Category], format_func=nice)
    min_sev = f3.selectbox("Minimum severity", [1, 2, 3, 4, 5], format_func=lambda v: f"{v}+ / 5")
    dept = f4.selectbox("Department", ["All", "Manual review"] + list(DEPARTMENTS.values()))

    a1, a2, _ = st.columns([1, 1, 3])
    n_demo = db.count_demo_reports()
    with a1:
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
    with a2:
        if st.button("Lock dashboard", width="stretch"):
            st.session_state.officer_ok = False
            st.rerun()

    reports = filter_reports(db.list_reports(status, dept), query, category, min_sev)
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
            dot = {"P1": "🔴", "P2": "🟠", "P3": "🟡", "P4": "🟢"}[r["priority_label"][:2]]
            title = (f"{dot} {prefix}#{r['id']} · {r['priority_label']} ({r['priority']}) · "
                     f"{nice(r['category'])} · sev {r['severity']}/5 · {r['area']} · "
                     f"{r['department']} · {r['status']}")
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


def filter_reports(reports: list[dict], query: str, category: str, min_sev: int) -> list[dict]:
    q = (query or "").strip().lower()
    out = []
    for r in reports:
        if category != "All" and r["category"] != category:
            continue
        if r["severity"] < min_sev:
            continue
        if q and q not in " ".join(str(r[k] or "") for k in ("area", "summary", "description")).lower():
            continue
        out.append(r)
    return out


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
    st.markdown('<div class="page-title"><h2>System metrics</h2>'
                '<p>Performance, efficiency and security figures recorded from real app activity.</p></div>',
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
    pri = pd.Series([r["priority_label"] for r in db.list_reports("All")]).value_counts().sort_index()
    if not pri.empty:
        st.markdown("**Priority distribution (all stored reports)**")
        st.bar_chart(pri.rename_axis("priority").reset_index(name="reports"), x="priority", y="reports",
                     height=220, color="#22d3ee")
    st.caption("Geographic hotspot map not shown: reports store a locality name, not GPS coordinates "
               "(photo GPS is deliberately removed for privacy).")
    st.markdown("**Security audit log (latest 20)**")
    st.dataframe(pd.DataFrame(m["audit"]), width="stretch", hide_index=True)


{"Report an issue": citizen_page, "Officer dashboard": officer_page, "System metrics": metrics_page}[page]()
