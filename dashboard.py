import streamlit as st
import sqlite3
import pandas as pd
import re
import plotly.express as px
from datetime import datetime

DB_NAME = "jobs.db"

st.set_page_config(page_title="Job Hunt", page_icon="🎯", layout="wide")

JOB_STATUSES = [
    "resume_generated",
    "awaiting response",
    "closed",
    "not_interested",
    "interviewing",
    "rejected",
    "offer",
]

STATUS_ICONS = {
    "resume_generated": "📄",
    "awaiting response": "🧍‍♂️⌛",
    "closed":            "🚫",
    "not_interested":    "👎",
    "interviewing":      "🗣️",
    "rejected":          "❌",
    "offer":             "🎉",
}

ROWS_APPROVED = 25
ROWS_RESUMES  = 10
ROWS_DENIED   = 25


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn():
    return sqlite3.connect(DB_NAME)


# --- Metrics (counts only, no row data) ------------------------------------

def load_metrics():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM approved_jobs")
    approved_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM approved_jobs WHERE applied = 1")
    applied_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM approved_jobs WHERE resume_text IS NOT NULL")
    resumes_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM approved_jobs WHERE job_status = 'interviewing'")
    interviewing_n = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM denied_jobs")
    denied_n = cur.fetchone()[0]
    conn.close()
    return approved_n, applied_n, resumes_n, interviewing_n, denied_n


# --- Approved tab ----------------------------------------------------------

def count_approved(unapplied_only: bool, status_filter: str) -> int:
    wheres, params = _approved_where(unapplied_only, status_filter)
    sql = f"SELECT COUNT(*) FROM approved_jobs" + (f" WHERE {' AND '.join(wheres)}" if wheres else "")
    conn = get_conn()
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return n


def load_approved_page(unapplied_only: bool, status_filter: str,
                       sort: str, page: int) -> pd.DataFrame:
    wheres, params = _approved_where(unapplied_only, status_filter)
    order = _approved_order(sort)
    where_sql = f" WHERE {' AND '.join(wheres)}" if wheres else ""
    sql = f"""
        SELECT job_id, title, company, score, approved_at,
               applied, job_status, notes
        FROM approved_jobs
        {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    params += [ROWS_APPROVED, (page - 1) * ROWS_APPROVED]
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def _approved_where(unapplied_only, status_filter):
    wheres, params = [], []
    if unapplied_only:
        wheres.append("applied != 1")
    if status_filter != "All":
        wheres.append("job_status = ?")
        params.append(status_filter)
    return wheres, params


def _approved_order(sort):
    return {
        "Date ↓":  "approved_at DESC",
        "Score ↓": "CAST(score AS REAL) DESC",
        "Score ↑": "CAST(score AS REAL) ASC",
    }.get(sort, "approved_at DESC")


# --- Resumes tab -----------------------------------------------------------

def count_resumes(sort: str) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM approved_jobs WHERE resume_text IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return n


def load_resumes_page(sort: str, page: int) -> pd.DataFrame:
    order = {
        "Date ↓":       "approved_at DESC",
        "Score ↓":      "CAST(score AS REAL) DESC",
        "Applied first": "applied DESC, approved_at DESC",
    }.get(sort, "approved_at DESC")
    sql = f"""
        SELECT job_id, title, company, score, approved_at,
               applied, job_status, resume_text
        FROM approved_jobs
        WHERE resume_text IS NOT NULL
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=[ROWS_RESUMES, (page - 1) * ROWS_RESUMES])
    conn.close()
    return df


# --- Denied tab ------------------------------------------------------------

def count_denied() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM denied_jobs").fetchone()[0]
    conn.close()
    return n


def load_denied_page(sort: str, page: int) -> pd.DataFrame:
    order = {
        "Date ↓":  "denied_at DESC",
        "Score ↑": "CAST(score AS REAL) ASC",
    }.get(sort, "denied_at DESC")
    sql = f"""
        SELECT job_id, title, company, score, reasoning, denied_at
        FROM denied_jobs
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=[ROWS_DENIED, (page - 1) * ROWS_DENIED])
    conn.close()
    return df


# --- Skills tab (already aggregated in SQL, keep as-is) --------------------

def load_skills() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("""
        SELECT skill, COUNT(*) as mentions
        FROM skill_mentions
        GROUP BY skill
        ORDER BY mentions DESC
        LIMIT 25
    """, conn)
    conn.close()
    return df


# --- Writes ----------------------------------------------------------------

def set_applied(job_id, value):
    conn = get_conn()
    conn.execute("UPDATE approved_jobs SET applied = ? WHERE job_id = ?",
                 (1 if value else 0, job_id))
    conn.commit()
    conn.close()


def set_status(job_id, status):
    conn = get_conn()
    conn.execute("""
        UPDATE approved_jobs SET job_status = ?, reviewed_at = ? WHERE job_id = ?
    """, (status, datetime.utcnow().isoformat(), job_id))
    conn.commit()
    conn.close()


def set_notes(job_id, notes):
    conn = get_conn()
    conn.execute("UPDATE approved_jobs SET notes = ? WHERE job_id = ?",
                 (notes, job_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def job_num(job_id):
    m = re.search(r'/jobs/view/(\d+)', str(job_id))
    return m.group(1) if m else str(job_id)[-12:]


def fmt_date(val):
    if not val:
        return ""
    try:
        return datetime.fromisoformat(str(val)).strftime("%b %d, %Y")
    except Exception:
        return str(val)[:10]


def score_color(score):
    try:
        s = float(score)
        if s >= 75: return "🟢"
        if s >= 55: return "🟡"
        return "🔴"
    except Exception:
        return "⚪"


def status_icon(status):
    return STATUS_ICONS.get(status or "pending", "⏳")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

approved_n, applied_n, resumes_n, interviewing_n, denied_n = load_metrics()

st.title("🎯 Job Hunt Dashboard")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Approved",     approved_n)
c2.metric("Applied",      applied_n)
c3.metric("Resumes",      resumes_n)
c4.metric("Interviewing", interviewing_n)
c5.metric("Denied",       denied_n)

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["Approved", "Resumes", "Denied", "Skills"])


# ---------------------------------------------------------------------------
# Approved
# ---------------------------------------------------------------------------
with tab1:
    if approved_n == 0:
        st.info("No approved jobs yet.")
    else:
        col1, col2, col3 = st.columns([2, 2, 2])
        with col1:
            show_unapplied = st.checkbox("Show unapplied only", value=False, key="show_unapplied")
        with col2:
            status_filter = st.selectbox("Filter by status", ["All"] + JOB_STATUSES, key="status_filter")
        with col3:
            sort1 = st.selectbox("Sort", ["Date ↓", "Score ↓", "Score ↑"], key="sort1")

        # Reset to page 1 when filters change
        filter_key = (show_unapplied, status_filter, sort1)
        if st.session_state.get("_approved_filter_key") != filter_key:
            st.session_state["approved_page"] = 1
            st.session_state["_approved_filter_key"] = filter_key

        total = count_approved(show_unapplied, status_filter)
        total_pages = max(1, (total + ROWS_APPROVED - 1) // ROWS_APPROVED)

        st.caption(f"{total} job(s)")

        page = st.pagination(num_pages=total_pages, key="approved_page")

        df = load_approved_page(show_unapplied, status_filter, sort1, page)

        start = (page - 1) * ROWS_APPROVED + 1
        st.caption(f"Showing {start}–{start + len(df) - 1}")

        h1, h2, h3, h4, h5 = st.columns([3, 1, 1, 2, 1])
        h1.markdown("**Job**")
        h2.markdown("**Score**")
        h3.markdown("**Status**")
        h4.markdown("**Date**")
        h5.markdown("**Applied**")
        st.divider()

        for _, row in df.iterrows():
            jid       = str(row["job_id"])
            title     = row.get("title") or f"#{job_num(jid)}"
            company   = row.get("company") or ""
            score     = row["score"]
            applied   = bool(row.get("applied", 0))
            status    = row.get("job_status") or "pending"
            notes     = row.get("notes") or ""
            icon      = score_color(score)
            score_str = f"{float(score):.0f}" if score is not None else "—"
            sicon     = status_icon(status)

            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 2, 1])
                c1.markdown(
                    f"{title} | {jid}" + (f"\n\n*{company}*" if company else "")
                )
                c2.markdown(f"{icon} {score_str}")
                c3.markdown(f"{sicon} {status}")
                c4.markdown(fmt_date(row.get("approved_at")))
                if applied:
                    if c5.button("✓", key=f"btn_{jid}", type="primary", help="Click to unmark"):
                        set_applied(jid, False)
                        st.rerun()
                else:
                    if c5.button("Apply", key=f"btn_{jid}"):
                        set_applied(jid, True)
                        st.rerun()

                with st.expander("Edit status / notes"):
                    new_status = st.selectbox(
                        "Status", JOB_STATUSES,
                        index=JOB_STATUSES.index(status) if status in JOB_STATUSES else 0,
                        key=f"status_{jid}"
                    )
                    new_notes = st.text_area("Notes", value=notes, key=f"notes_{jid}", height=80)
                    if st.button("Save", key=f"save_{jid}"):
                        set_status(jid, new_status)
                        set_notes(jid, new_notes)
                        st.rerun()


# ---------------------------------------------------------------------------
# Resumes
# ---------------------------------------------------------------------------
with tab2:
    if resumes_n == 0:
        st.info("No resumes generated yet.")
    else:
        sort2 = st.selectbox("Sort", ["Date ↓", "Score ↓", "Applied first"], key="sort2")

        filter_key2 = (sort2,)
        if st.session_state.get("_resume_filter_key") != filter_key2:
            st.session_state["resume_page"] = 1
            st.session_state["_resume_filter_key"] = filter_key2

        total2 = count_resumes(sort2)
        total_pages2 = max(1, (total2 + ROWS_RESUMES - 1) // ROWS_RESUMES)

        st.caption(f"{total2} resume(s)")

        page2 = st.pagination(num_pages=total_pages2, key="resume_page")

        resume_df = load_resumes_page(sort2, page2)

        for _, row in resume_df.iterrows():
            jid       = str(row["job_id"])
            title     = row.get("title") or f"#{job_num(jid)}"
            company   = row.get("company") or ""
            score     = row["score"]
            applied   = bool(row.get("applied", 0))
            status    = row.get("job_status") or "pending"
            icon      = score_color(score)
            score_str = f"{float(score):.0f}" if score is not None else "—"
            tick      = " ✓" if applied else ""
            sicon     = status_icon(status)

            header = (
                f"{title} ({jid})"
                f"{(' — ' + company) if company else ''}  |  "
                f"{icon} {score_str}  |  "
                f"{sicon} {status}  |  "
                f"{fmt_date(row.get('approved_at'))}"
                f"{tick}"
            )
            with st.expander(header, expanded=False):
                st.markdown(f"**URL:** {jid}")
                if applied:
                    if st.button("✓ Applied — click to undo", key=f"res_btn_{jid}", type="primary"):
                        set_applied(jid, False)
                        st.rerun()
                else:
                    if st.button("Mark as Applied", key=f"res_btn_{jid}"):
                        set_applied(jid, True)
                        st.rerun()
                st.divider()
                st.markdown(row["resume_text"])


# ---------------------------------------------------------------------------
# Denied
# ---------------------------------------------------------------------------
with tab3:
    if denied_n == 0:
        st.info("No denied jobs.")
    else:
        sort3 = st.selectbox("Sort", ["Date ↓", "Score ↑"], key="sort3")

        filter_key3 = (sort3,)
        if st.session_state.get("_denied_filter_key") != filter_key3:
            st.session_state["denied_page"] = 1
            st.session_state["_denied_filter_key"] = filter_key3

        total3 = count_denied()
        total_pages3 = max(1, (total3 + ROWS_DENIED - 1) // ROWS_DENIED)

        st.caption(f"{total3} denied job(s)")

        page3 = st.pagination(num_pages=total_pages3, key="denied_page")

        denied_df = load_denied_page(sort3, page3)

        h1, h2, h3 = st.columns([3, 1, 2])
        h1.markdown("**Job**")
        h2.markdown("**Score**")
        h3.markdown("**Date**")
        st.divider()

        for _, row in denied_df.iterrows():
            jid       = str(row["job_id"])
            title     = row.get("title") or f"#{job_num(jid)}"
            company   = row.get("company") or ""
            score     = row["score"]
            icon      = score_color(score)
            score_str = f"{float(score):.0f}" if score is not None else "—"

            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 1, 2])
                c1.markdown(
                    f"{title} | {jid}" + (f"\n\n*{company}*" if company else "")
                )
                c2.markdown(f"{icon} {score_str}")
                c3.markdown(fmt_date(row.get("denied_at")))
                with st.expander("Reasoning"):
                    st.markdown(row.get("reasoning") or "No reasoning provided")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
with tab4:
    skills_df = load_skills()
    if skills_df.empty:
        st.info("No skill data yet.")
    else:
        st.caption(f"{len(skills_df)} unique skills tracked (top 25)")
        fig = px.bar(
            skills_df,
            x="mentions", y="skill",
            orientation="h",
            title="Top 25 Skills"
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)
        st.table(skills_df.set_index("skill")["mentions"])