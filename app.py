"""
app.py — HirePilot main Streamlit application.

Architecture
------------
- require_auth() gate at the top: no unauthenticated user reaches any data
- Every DB call passes user_id — no cross-user data access possible
- Deferred modules (Gmail, job discovery, referral, automation) are NOT imported
- All user text is validated/sanitized before DB writes or AI calls
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import streamlit as st

from auth import require_auth, sign_out
from database import Database, ALL_STATUSES, STATUS_GROUP
from sanitize import (
    ValidationError,
    validate_company, validate_role, validate_url,
    validate_api_key, clean_text,
    sanitize_for_prompt,
    MAX_JD_CHARS, MAX_NOTES_CHARS, MAX_RESUME_CHARS,
)
from scoring import score_resume_jd
from resume_tailor import tailor_resume, quality_check
from cover_letter import generate_cover_letter
from candidate_profile import (
    get_profile, save_profile, is_profile_complete,
    get_visa_note, WORK_AUTH_OPTIONS,
)

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("hirepilot.app")

# ── Page config ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="HirePilot",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
:root {
  --bg:#0f1117;--surface:#1a1d27;--border:#2a2d3e;
  --accent:#6c63ff;--accent2:#8b85ff;
  --text:#e8e9f0;--muted:#7a7d9c;
  --green:#2ecc71;--yellow:#f39c12;--red:#e74c3c;--blue:#5b9bd5;
}
.stApp{background:var(--bg);}
[data-testid="stSidebar"]{background:var(--surface);border-right:1px solid var(--border);}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 16px;text-align:center;}
.kpi .v{font-size:2rem;font-weight:700;color:var(--accent2);line-height:1;}
.kpi .l{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-top:5px;}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:600;white-space:nowrap;}
.b-neutral{background:#2a2d3e;color:#9a9dbf;}
.b-active{background:#1e3a5f;color:var(--blue);}
.b-progress{background:#1e3d2b;color:var(--green);}
.b-success{background:#3d3000;color:var(--yellow);}
.b-danger{background:#3d1a1a;color:var(--red);}
.b-stale{background:#2d2d2d;color:#777;}
.sc{font-weight:700;font-size:1rem;}
.sc-hi{color:var(--green);}.sc-md{color:var(--yellow);}.sc-lo{color:var(--red);}
.output-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  padding:18px;font-family:'Courier New',monospace;font-size:.83rem;
  color:var(--text);white-space:pre-wrap;max-height:450px;overflow-y:auto;}
.pipe-bar{display:flex;height:8px;border-radius:6px;overflow:hidden;margin:10px 0;}
.pb-seg{height:100%;}
.stTabs [data-baseweb="tab-list"]{gap:8px;background:transparent;}
.stTabs [data-baseweb="tab"]{background:var(--surface);border:1px solid var(--border);
  border-radius:20px;color:var(--muted);padding:5px 16px;}
.stTabs [aria-selected="true"]{background:var(--accent)!important;color:#fff!important;
  border-color:var(--accent)!important;}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════════════
db = Database()

# Auth gate — MUST be before any data access
user_id = require_auth(db)

# Pull profile once per session for auto-fill
if "profile" not in st.session_state:
    st.session_state["profile"] = get_profile(db, user_id)


def profile() -> dict:
    return st.session_state.get("profile", {})


# ── Helpers ────────────────────────────────────────────────────────────
def _badge(status: str) -> str:
    grp = STATUS_GROUP.get(status, "neutral")
    return f'<span class="badge b-{grp}">{status}</span>'

def _score_chip(score) -> str:
    if score is None:
        return '<span style="color:#555">—</span>'
    cls = "sc-hi" if score >= 70 else ("sc-md" if score >= 45 else "sc-lo")
    return f'<span class="sc {cls}">{score}%</span>'

def _pipeline_bar(counts: dict) -> str:
    colors = {"neutral":"#3a3d5e","active":"#5b9bd5","progress":"#2ecc71",
              "success":"#f39c12","danger":"#e74c3c","stale":"#555"}
    total = sum(counts.values()) or 1
    segs = []
    for status, cnt in counts.items():
        if cnt == 0: continue
        grp = STATUS_GROUP.get(status, "neutral")
        pct = cnt / total * 100
        segs.append(
            f'<div class="pb-seg" style="width:{pct:.1f}%;background:{colors[grp]}" '
            f'title="{status}: {cnt}"></div>'
        )
    return f'<div class="pipe-bar">{"".join(segs)}</div>'

def _get_api_key() -> str:
    return st.session_state.get("api_key", "")

def _api_key_error():
    st.error("No API key set. Add your Anthropic API key in **👤 Profile**.")


# ── Sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🚀 HirePilot")
    user_name = st.session_state.get("user_name", "")
    if user_name:
        st.caption(f"Signed in as {user_name}")
    st.markdown("---")

    page = st.radio("nav", [
        "📋 My Applications",
        "🎯 Score & Apply",
        "📄 Saved Documents",
        "👤 Profile",
    ], label_visibility="collapsed")

    st.markdown("---")
    if st.button("Sign out", use_container_width=True):
        sign_out()

    # Unread notifications (non-blocking)
    try:
        unread = db.get_unread_count(user_id)
        if unread:
            st.markdown(
                f'<div style="background:#3d2f00;border:1px solid #f39c12;'
                f'border-radius:8px;padding:7px 12px;font-size:.78rem;color:#f39c12">'
                f'🔔 {unread} unread</div>',
                unsafe_allow_html=True,
            )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════
# PAGE: MY APPLICATIONS
# ══════════════════════════════════════════════════════════════════════
if page == "📋 My Applications":
    st.markdown("## 📋 My Applications")

    try:
        summary = db.get_pipeline_summary(user_id)
    except Exception as e:
        logger.exception("Pipeline summary failed")
        st.error("Could not load dashboard. Please refresh.")
        st.stop()

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    for col, val, lbl in [
        (c1, summary["total"],    "Total"),
        (c2, summary["active"],   "Active"),
        (c3, summary["offers"],   "Offers"),
        (c4, summary["rejected"], "Rejected"),
    ]:
        col.markdown(
            f'<div class="kpi"><div class="v">{val}</div>'
            f'<div class="l">{lbl}</div></div>',
            unsafe_allow_html=True,
        )
    st.markdown(_pipeline_bar(summary["counts"]), unsafe_allow_html=True)
    st.markdown("---")

    # Add Job expander (inline — no separate page)
    with st.expander("➕ Add new application", expanded=False):
        with st.form("add_job_form", clear_on_submit=True):
            ac1, ac2 = st.columns(2)
            aj_company = ac1.text_input("Company *", placeholder="Stripe")
            aj_role    = ac2.text_input("Role *", placeholder="Data Analyst")
            aj_url     = st.text_input("Job URL", placeholder="https://…")
            aj_jd      = st.text_area("Job description", height=140,
                                      placeholder="Paste the JD here…")
            adc1, adc2 = st.columns(2)
            aj_location = adc1.text_input("Location", placeholder="Remote / NYC")
            aj_source   = adc2.text_input("Source", placeholder="LinkedIn")

            with st.expander("More details"):
                am1, am2 = st.columns(2)
                aj_date_found   = am1.date_input("Date found", value=datetime.today())
                aj_date_applied = am2.date_input("Date applied", value=None)
                aj_recruiter    = am1.text_input("Recruiter email")
                aj_notes        = st.text_area("Notes", height=60)

            if st.form_submit_button("💾 Save application", use_container_width=True):
                try:
                    company  = validate_company(aj_company)
                    role     = validate_role(aj_role)
                    job_url  = validate_url(aj_url)
                    jd       = clean_text(aj_jd, MAX_JD_CHARS, "job description")
                    location = clean_text(aj_location, 200, "location")
                    source   = clean_text(aj_source, 200, "source")
                    notes    = clean_text(aj_notes, MAX_NOTES_CHARS, "notes")
                    recruiter = clean_text(aj_recruiter, 200, "recruiter email")

                    jid = db.add_job(
                        user_id=user_id,
                        company=company, role=role,
                        location=location, job_url=job_url,
                        source=source, job_description=jd,
                        date_found=str(aj_date_found),
                        date_applied=str(aj_date_applied) if aj_date_applied else None,
                        recruiter_email=recruiter, notes=notes,
                    )
                    # Auto-score if JD provided
                    if jd and profile().get("resume_text"):
                        with st.spinner("Scoring…"):
                            try:
                                r = score_resume_jd(profile()["resume_text"], jd)
                                db.update_match_score(user_id, jid, r["score"])
                                st.success(f"✅ Saved **{role}** at **{company}** — ATS score: {r['score']}%")
                            except Exception:
                                st.success(f"✅ Saved **{role}** at **{company}**")
                    else:
                        st.success(f"✅ Saved **{role}** at **{company}**")
                    st.rerun()
                except ValidationError as e:
                    st.error(str(e))
                except Exception:
                    logger.exception("Failed to save job")
                    st.error("Failed to save. Please try again.")

    # Job list
    try:
        all_jobs = db.get_all_jobs(user_id)
    except Exception:
        logger.exception("get_all_jobs failed")
        st.error("Could not load applications. Please refresh.")
        st.stop()

    if not all_jobs:
        st.info("No applications yet. Use the **➕ Add new application** panel above.")
    else:
        fc1, fc2, fc3 = st.columns([2, 2, 1])
        companies = sorted({j["company"] for j in all_jobs})
        f_status  = fc1.selectbox("Status", ["All"] + ALL_STATUSES)
        f_company = fc2.selectbox("Company", ["All"] + companies)
        f_sort    = fc3.selectbox("Sort", ["Newest", "Score ↓"])

        jobs = all_jobs
        if f_status  != "All": jobs = [j for j in jobs if j["status"] == f_status]
        if f_company != "All": jobs = [j for j in jobs if j["company"] == f_company]
        if f_sort == "Score ↓":
            jobs = sorted(jobs, key=lambda j: j.get("match_score") or -1, reverse=True)

        st.caption(f"Showing **{len(jobs)}** of **{len(all_jobs)}**")

        h1, h2, h3, h4, h5, h6 = st.columns([3, 1.5, 1, 1.5, 1.5, 1])
        for col, lbl in zip([h1,h2,h3,h4,h5,h6],
                            ["Company / Role","Status","Score","Applied","Update",""]):
            col.markdown(
                f'<div style="font-size:.72rem;color:#7a7d9c;text-transform:uppercase">{lbl}</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<hr style="margin:2px 0 6px;border-color:#2a2d3e">', unsafe_allow_html=True)

        for j in jobs:
            jid = j["id"]
            c1, c2, c3, c4, c5, c6 = st.columns([3, 1.5, 1, 1.5, 1.5, 1])

            url_s = f'<a href="{j["job_url"]}" target="_blank" style="text-decoration:none;color:inherit">' if j.get("job_url") else ""
            url_e = "</a>" if j.get("job_url") else ""
            c1.markdown(
                f'{url_s}<span style="font-weight:700">{j["company"]}</span>{url_e}'
                f'<div style="font-size:.8rem;color:#7a7d9c">{j["role"]}</div>',
                unsafe_allow_html=True,
            )
            c2.markdown(f'<div style="margin-top:6px">{_badge(j["status"])}</div>', unsafe_allow_html=True)
            c3.markdown(f'<div style="margin-top:6px">{_score_chip(j.get("match_score"))}</div>', unsafe_allow_html=True)
            c4.markdown(
                f'<div style="font-size:.8rem;color:#7a7d9c;margin-top:8px">'
                f'{(j.get("date_applied") or "—")}</div>',
                unsafe_allow_html=True,
            )

            cur_idx = ALL_STATUSES.index(j["status"]) if j["status"] in ALL_STATUSES else 0
            new_status = c5.selectbox("", ALL_STATUSES, index=cur_idx,
                                      key=f"st_{jid}", label_visibility="collapsed")
            if new_status != j["status"]:
                try:
                    db.update_status(user_id, jid, new_status)
                    st.rerun()
                except Exception:
                    logger.exception("update_status failed")
                    st.error("Could not update status.")

            if c6.button("🗑", key=f"del_{jid}", help="Delete"):
                try:
                    db.delete_job(user_id, jid)
                    st.rerun()
                except Exception:
                    logger.exception("delete_job failed")
                    st.error("Could not delete.")

            st.markdown('<hr style="margin:3px 0;border-color:#1e2130">', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE: SCORE & APPLY
# ══════════════════════════════════════════════════════════════════════
elif page == "🎯 Score & Apply":
    st.markdown("## 🎯 Score & Apply")
    st.caption("Score your resume against a job, tailor it, and generate a cover letter — all in one place.")

    # ── ATS Scorer ────────────────────────────────────────────────────
    st.markdown("### 1. Score your resume")

    saved_resume = profile().get("resume_text", "")
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**Your resume**")
        resume_text = st.text_area(
            "", height=260, value=saved_resume,
            placeholder="Paste your resume…",
            key="ats_resume", label_visibility="collapsed",
        )
        if saved_resume and not resume_text:
            resume_text = saved_resume
    with col_r:
        st.markdown("**Job description**")
        jd_text = st.text_area(
            "", height=260, placeholder="Paste the job description…",
            key="ats_jd", label_visibility="collapsed",
        )

    # Link to job (optional)
    all_jobs = db.get_all_jobs(user_id)
    job_opts = ["— don't save —"] + [
        f"{j['company']} — {j['role']} (#{j['id']})" for j in all_jobs
    ]
    save_to = st.selectbox("Save score to tracked job (optional)", job_opts)

    if st.button("⚡ Score now", use_container_width=True, type="primary"):
        try:
            safe_resume = sanitize_for_prompt(resume_text, MAX_RESUME_CHARS, "resume")
            safe_jd     = sanitize_for_prompt(jd_text, MAX_JD_CHARS, "job description")
            if not safe_resume or not safe_jd:
                st.error("Paste both your resume and the job description.")
            else:
                with st.spinner("Analysing…"):
                    r = score_resume_jd(safe_resume, safe_jd)

                st.session_state.update({
                    "last_score": r,
                    "last_resume": safe_resume,
                    "last_jd": safe_jd,
                })

                if r["hard_rejects"]:
                    for msg in r["hard_rejects"]:
                        st.error(f"🚫 {msg}")

                sc = r["score"]
                col_score, col_info, col_grade = st.columns([1, 2, 2])
                sc_color = "#2ecc71" if sc >= 70 else ("#f39c12" if sc >= 45 else "#e74c3c")
                col_score.markdown(
                    f'<div style="text-align:center;font-size:2.5rem;font-weight:700;'
                    f'color:{sc_color}">{sc}%</div>'
                    f'<div style="text-align:center;font-size:.75rem;color:#7a7d9c">match score</div>',
                    unsafe_allow_html=True,
                )
                col_info.markdown(
                    f"**{r['role_type']} role**  \n"
                    f"Matched **{r['keyword_hits']}** of **{r['total_keywords']}** keywords  \n"
                    f"{'🌟 Preferred role type' if r['is_preferred_role'] else ''}"
                )
                qc = r["quality_check"]
                col_grade.markdown(
                    f"**Resume grade: {qc['grade']}**  \n"
                    f"{qc['passes']} pass · {qc['warns']} warn · {qc['fails']} fail"
                )

                t1, t2, t3 = st.tabs(["❌ Missing keywords", "✅ Matched", "💡 Recommendations"])
                with t1:
                    if r["missing_keywords"]:
                        cols = st.columns(4)
                        for i, kw in enumerate(r["missing_keywords"]):
                            cols[i % 4].markdown(f"`{kw}`")
                    else:
                        st.success("No critical gaps!")
                with t2:
                    cols = st.columns(4)
                    for i, kw in enumerate(sorted(r["matched_keywords"])):
                        cols[i % 4].markdown(f"✅ `{kw}`")
                with t3:
                    for ch in r.get("recommended_changes", []):
                        st.markdown(f"- {ch}")

                if save_to != "— don't save —":
                    try:
                        jid = int(save_to.split("#")[-1].rstrip(")"))
                        db.update_match_score(user_id, jid, sc)
                        st.success(f"Score saved to job #{jid}.")
                    except Exception:
                        logger.exception("save score failed")
        except ValidationError as e:
            st.error(str(e))
        except Exception:
            logger.exception("ATS scoring failed")
            st.error("Scoring failed. Please try again.")

    st.markdown("---")

    # ── Resume Tailor ─────────────────────────────────────────────────
    st.markdown("### 2. Tailor your resume (AI)")

    prior_score  = st.session_state.get("last_score")
    prior_resume = st.session_state.get("last_resume", profile().get("resume_text", ""))
    prior_jd     = st.session_state.get("last_jd", "")

    if prior_score:
        sc_val = prior_score["score"]
        sc_col = "#2ecc71" if sc_val >= 70 else ("#f39c12" if sc_val >= 45 else "#e74c3c")
        st.caption(
            f"ATS score loaded: "
            f"<span style='color:{sc_col};font-weight:700'>{sc_val}%</span>",
            unsafe_allow_html=True,
        )

    tl1, tl2 = st.columns(2)
    with tl1:
        st.markdown("**Resume**")
        t_resume = st.text_area("", height=220, value=prior_resume,
                                key="tailor_resume", label_visibility="collapsed")
    with tl2:
        st.markdown("**Job description**")
        t_jd = st.text_area("", height=220, value=prior_jd,
                             key="tailor_jd", label_visibility="collapsed")

    t_role = st.text_input("Target role", placeholder="Data Analyst")

    ta1, ta2 = st.columns(2)
    run_tailor = ta1.button("✨ Tailor resume", use_container_width=True, type="primary")
    run_qc     = ta2.button("🔍 Quality check only", use_container_width=True)

    if run_tailor or run_qc:
        api_key = _get_api_key()
        if not api_key:
            _api_key_error()
        else:
            try:
                safe_resume = sanitize_for_prompt(t_resume, MAX_RESUME_CHARS, "resume")
                safe_jd     = sanitize_for_prompt(t_jd, MAX_JD_CHARS, "job description")
                safe_role   = clean_text(t_role, 200, "target role")

                if not safe_resume:
                    st.error("Resume is required.")
                elif run_tailor and not safe_jd:
                    st.error("Job description is required for tailoring.")
                else:
                    if run_tailor:
                        with st.spinner("Tailoring resume… (15-30 seconds)"):
                            result = tailor_resume(safe_resume, safe_jd, safe_role,
                                                   api_key, prior_score)
                        tailored = result["tailored_resume"]
                        st.markdown("**Tailored resume**")
                        edited = st.text_area("Edit before saving", value=tailored,
                                              height=300, key="tailored_edit")

                        ec1, ec2 = st.columns(2)
                        if ec1.download_button("⬇️ Download .txt", edited,
                                               file_name="tailored_resume.txt"):
                            pass
                        if ec2.button("💾 Save to documents"):
                            try:
                                db.save_document(user_id, "Tailored Resume",
                                                 "", safe_role, edited)
                                st.success("Saved to documents.")
                            except Exception:
                                logger.exception("save_document failed")
                                st.error("Could not save. Please try again.")

                        st.markdown("**What changed**")
                        for line in result["notes"].split("\n"):
                            if line.strip():
                                st.markdown(f"- {line.strip().lstrip('-').strip()}")

                    if run_qc or run_tailor:
                        qc_text = edited if (run_tailor and "edited" in dir()) else safe_resume
                        with st.spinner("Running quality check…"):
                            qc_r = quality_check(qc_text, safe_jd or "No JD.", api_key)
                        grade   = qc_r.get("overall_grade", "?")
                        verdict = qc_r.get("final_verdict", "?")
                        st.markdown(f"**Grade: {grade} · {verdict}**")
                        st.caption(qc_r.get("summary", ""))
                        qa1, qa2, qa3 = st.columns(3)
                        with qa1:
                            st.markdown("**✅ Strengths**")
                            for s in qc_r.get("strengths", []): st.markdown(f"- {s}")
                        with qa2:
                            st.markdown("**⚠️ Weaknesses**")
                            for w in qc_r.get("weaknesses", []): st.markdown(f"- {w}")
                        with qa3:
                            st.markdown("**🤖 ATS risks**")
                            for a in qc_r.get("ats_risks", []): st.markdown(f"- {a}")

            except ValidationError as e:
                st.error(str(e))
            except ValueError as e:
                st.error(str(e))
            except Exception:
                logger.exception("Tailor/QC failed")
                st.error("Request failed. Please try again.")

    st.markdown("---")

    # ── Cover Letter ──────────────────────────────────────────────────
    st.markdown("### 3. Generate cover letter (AI)")

    p = profile()
    cl1, cl2 = st.columns(2)
    cl_company   = cl1.text_input("Company *", placeholder="Stripe", key="cl_co")
    cl_role      = cl2.text_input("Role *", placeholder="Data Analyst", key="cl_role")
    cl_manager   = cl1.text_input("Hiring manager (optional)", key="cl_mgr")
    cl_name      = cl2.text_input("Your name *", value=p.get("name", ""), key="cl_name")
    cl_tone      = cl1.selectbox("Tone", ["Professional","Enthusiastic","Concise","Creative"])
    cl_jd        = st.text_area("Job description *", height=130,
                                value=st.session_state.get("last_jd", ""), key="cl_jd")
    cl_summary   = st.text_area("Your key experience (honest facts only)",
                                value=p.get("summary", ""), height=100, key="cl_summary")

    if st.button("✉️ Generate cover letter", use_container_width=True, type="primary"):
        api_key = _get_api_key()
        if not api_key:
            _api_key_error()
        else:
            try:
                safe_co   = validate_company(cl_company)
                safe_role = validate_role(cl_role)
                safe_jd   = sanitize_for_prompt(cl_jd, MAX_JD_CHARS, "job description")
                safe_name = clean_text(cl_name, 100, "name") or p.get("name", "")
                if not safe_jd:
                    st.error("Job description is required.")
                else:
                    with st.spinner("Writing cover letter…"):
                        letter = generate_cover_letter(
                            company=safe_co, role=safe_role, jd_text=safe_jd,
                            candidate_name=safe_name, hiring_manager=cl_manager,
                            resume_summary=cl_summary, tone=cl_tone,
                            api_key=api_key,
                            scoring_result=st.session_state.get("last_score"),
                        )
                    wc = len(letter.split())
                    wc_color = "#2ecc71" if wc <= 220 else "#f39c12"
                    st.markdown(
                        f'<span style="color:{wc_color};font-size:.8rem">Word count: {wc}</span>',
                        unsafe_allow_html=True,
                    )
                    edited_cl = st.text_area("Edit before saving", value=letter,
                                             height=260, key="cl_edit")
                    cc1, cc2 = st.columns(2)
                    cc1.download_button("⬇️ Download .txt", edited_cl,
                                       file_name=f"cover_letter_{safe_co}.txt")
                    if cc2.button("💾 Save to documents"):
                        try:
                            db.save_document(user_id, "Cover Letter",
                                             safe_co, safe_role, edited_cl)
                            st.success("Saved to documents.")
                        except Exception:
                            logger.exception("save cover letter failed")
                            st.error("Could not save. Please try again.")
            except ValidationError as e:
                st.error(str(e))
            except ValueError as e:
                st.error(str(e))
            except Exception:
                logger.exception("cover letter failed")
                st.error("Request failed. Please try again.")


# ══════════════════════════════════════════════════════════════════════
# PAGE: SAVED DOCUMENTS
# ══════════════════════════════════════════════════════════════════════
elif page == "📄 Saved Documents":
    st.markdown("## 📄 Saved Documents")
    try:
        docs = db.get_all_documents(user_id)
    except Exception:
        logger.exception("get_all_documents failed")
        st.error("Could not load documents. Please refresh.")
        st.stop()

    if not docs:
        st.info("No saved documents yet. Generate a tailored resume or cover letter in **🎯 Score & Apply**.")
    else:
        for doc in docs:
            with st.expander(
                f"{doc['doc_type']} — {doc.get('company','')} {doc.get('role','')} — {doc['created_at'][:10]}"
            ):
                st.text(doc["content"])
                dc1, dc2 = st.columns(2)
                dc1.download_button(
                    "⬇️ Download",
                    doc["content"],
                    file_name=f"{doc['doc_type'].lower().replace(' ','_')}.txt",
                    key=f"dl_{doc['id']}",
                )
                if dc2.button("🗑️ Delete", key=f"deldoc_{doc['id']}"):
                    try:
                        db.delete_document(user_id, doc["id"])
                        st.rerun()
                    except Exception:
                        logger.exception("delete_document failed")
                        st.error("Could not delete.")


# ══════════════════════════════════════════════════════════════════════
# PAGE: PROFILE
# ══════════════════════════════════════════════════════════════════════
elif page == "👤 Profile":
    st.markdown("## 👤 My Profile")
    p = get_profile(db, user_id)

    complete, missing = is_profile_complete(db, user_id)
    if not complete:
        st.warning(f"Complete your profile to enable all features. Missing: **{', '.join(missing)}**")

    tab_personal, tab_resume, tab_ai = st.tabs(["Personal info", "Resume", "AI settings"])

    with tab_personal:
        with st.form("profile_personal"):
            pc1, pc2 = st.columns(2)
            pf_name     = pc1.text_input("Full name *",   value=p.get("name", ""))
            pf_email    = pc2.text_input("Email",         value=p.get("email", ""), disabled=True)
            pf_phone    = pc1.text_input("Phone",         value=p.get("phone", ""))
            pf_location = pc2.text_input("Location",      value=p.get("location", ""),
                                         placeholder="Chicago, IL")
            wa_opts = WORK_AUTH_OPTIONS
            wa_idx  = wa_opts.index(p.get("work_auth","")) if p.get("work_auth","") in wa_opts else 0
            pf_auth = st.selectbox("Work authorization", wa_opts, index=wa_idx)
            pf_school   = st.text_input("University", value=p.get("school", ""),
                                        placeholder="Your university")
            pf_degree   = st.text_input("Degree",    value=p.get("degree", ""))
            pf_grad     = st.text_input("Graduation year", value=p.get("grad_year", ""))
            pf_linkedin = st.text_input("LinkedIn URL", value=p.get("linkedin", ""),
                                        placeholder="https://linkedin.com/in/…")

            if st.form_submit_button("💾 Save personal info", use_container_width=True):
                try:
                    updates = {**p,
                        "name": clean_text(pf_name, 100, "name"),
                        "phone": clean_text(pf_phone, 50, "phone"),
                        "location": clean_text(pf_location, 200, "location"),
                        "work_auth": pf_auth,
                        "school": clean_text(pf_school, 200, "school"),
                        "degree": clean_text(pf_degree, 200, "degree"),
                        "grad_year": clean_text(pf_grad, 10, "grad year"),
                        "linkedin": validate_url(pf_linkedin) if pf_linkedin.strip() else "",
                    }
                    save_profile(db, user_id, updates)
                    st.session_state["profile"] = get_profile(db, user_id)
                    st.success("✅ Personal info saved.")
                except ValidationError as e:
                    st.error(str(e))
                except Exception:
                    logger.exception("save profile failed")
                    st.error("Could not save. Please try again.")

    with tab_resume:
        st.caption(
            "Paste your resume as plain text. It auto-fills the ATS scorer and resume tailor."
        )
        pf_resume = st.text_area("Full resume *", value=p.get("resume_text",""),
                                 height=380,
                                 placeholder="Your Name\nyour.email@example.com | (555) 000-0000\n\n"
                                             "EXPERIENCE\n...\n\nEDUCATION\n...\n\nSKILLS\n...")
        wc = len(pf_resume.split())
        wc_color = "#2ecc71" if 150 <= wc <= 1000 else "#f39c12"
        st.markdown(
            f'<span style="color:{wc_color};font-size:.8rem">{wc} words</span>',
            unsafe_allow_html=True,
        )
        pf_summary = st.text_area(
            "3-5 line summary (used in cover letters)",
            value=p.get("summary",""), height=110,
            placeholder="• 2 years Data Analyst — SQL, Excel, Tableau\n"
                        "• BSc Business Administration, 2023\n"
                        "• Proficient in Python (beginner), Jira, PowerPoint",
        )
        if st.button("💾 Save resume", use_container_width=True, type="primary"):
            try:
                safe_resume  = sanitize_for_prompt(pf_resume,  MAX_RESUME_CHARS, "resume")
                safe_summary = sanitize_for_prompt(pf_summary, 2_000, "summary")
                updates = {**p, "resume_text": safe_resume, "summary": safe_summary}
                save_profile(db, user_id, updates)
                st.session_state["profile"] = get_profile(db, user_id)
                st.success("✅ Resume saved.")
            except ValidationError as e:
                st.error(str(e))
            except Exception:
                logger.exception("save resume failed")
                st.error("Could not save. Please try again.")

    with tab_ai:
        st.markdown("### Anthropic API key")
        st.caption(
            "Required for Resume Tailor and Cover Letter. "
            "Your key is encrypted and stored securely. "
            "Get a key at [console.anthropic.com](https://console.anthropic.com)."
        )
        current_key = _get_api_key()
        display = f"sk-ant-•••••{current_key[-4:]}" if current_key else "Not set"
        st.markdown(f"**Current key:** `{display}`")
        new_key = st.text_input("New API key", type="password", placeholder="sk-ant-…")
        if st.button("💾 Save API key"):
            try:
                validated = validate_api_key(new_key)
                if validated:
                    from auth import encrypt_api_key
                    enc = encrypt_api_key(validated)
                    db.set_user_pref(user_id, "anthropic_key_enc", enc)
                    st.session_state["api_key"] = validated
                    st.success("✅ API key saved and encrypted.")
                else:
                    st.error("Please enter a key.")
            except ValidationError as e:
                st.error(str(e))
            except Exception:
                logger.exception("save api key failed")
                st.error("Could not save key. Please try again.")

        st.markdown("---")
        st.markdown("### Account")
        if st.button("🗑️ Delete my account and all data", type="secondary"):
            st.warning(
                "This permanently deletes your account, applications, resumes, and documents. "
                "This cannot be undone."
            )
            if st.checkbox("I understand, delete my account"):
                try:
                    db.delete_user_and_all_data(user_id)
                    sign_out()
                except Exception:
                    logger.exception("delete account failed")
                    st.error("Could not delete account. Contact support.")
