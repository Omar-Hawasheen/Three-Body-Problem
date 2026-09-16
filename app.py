"""
Planner Agent — Web Interface
------------------------------
A small Streamlit app that sits on top of planner_agent.py so you can:
  - add tasks and fixed commitments through a form (no JSON editing)
  - toggle between rule-based and AI Agent (LLM) scheduling
  - see the plan rendered as a visual day-timeline (Google Calendar style)
  - hit Retry to get a different valid arrangement
  - hit Accept to push the schedule straight into your own Google Calendar

Run locally:
    pip install -r requirements.txt
    streamlit run app.py

Deploy for free:
    Push this folder to a GitHub repo and deploy on
    https://share.streamlit.io (Streamlit Community Cloud, free tier).
"""

import json
import os
import datetime as dt
from dataclasses import asdict

import streamlit as st

from planner_agent import Task, FixedCommitment, plan_day
from calendar_view import render_calendar_html
from weekly_schedule import DAY_ORDER, commitments_for, day_name_for_date
from google_calendar import build_auth_url, exchange_code_for_token, create_event, get_email

st.set_page_config(page_title="Planner Agent", page_icon="🗓️", layout="wide")


def _get_secret(name: str):
    """Secrets store first (Streamlit Cloud), env var second (local)."""
    try:
        val = st.secrets.get(name)
    except Exception:
        val = None
    return val or os.environ.get(name)


# --------------------------------------------------------------------------
# Session state setup
# --------------------------------------------------------------------------

for key, default in {
    "tasks": [],
    "commitments": [],
    "result": None,
    "mode_used": None,
    "mode_error": None,
    "gcal_access_token": None,
    "gcal_email": None,
    "gcal_connect_error": None,
    "loaded_day": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def load_example():
    st.session_state.tasks = [
        {"name": "Work on HPC project pipeline", "priority": "urgent", "duration_minutes": 120, "category": "project"},
        {"name": "Reply to unread emails", "priority": "important", "duration_minutes": 20, "category": "email"},
        {"name": "Review Discrete Math homework", "priority": "important", "duration_minutes": 45, "category": "study"},
        {"name": "Buy groceries", "priority": "low", "duration_minutes": 30, "category": "errand"},
        {"name": "Read one paper for stats course", "priority": "low", "duration_minutes": 40, "category": "study"},
    ]
    st.session_state.commitments = [
        {"name": "HPC & Big Data lecture", "start": "10:00", "end": "11:30"},
        {"name": "Badminton", "start": "17:00", "end": "18:30"},
    ]
    st.session_state.result = None


# --------------------------------------------------------------------------
# Google OAuth setup + callback handling (must run before any UI renders,
# since it may consume ?code=... from the URL and rerun)
# --------------------------------------------------------------------------

google_client_id = _get_secret("GOOGLE_CLIENT_ID")
google_client_secret = _get_secret("GOOGLE_CLIENT_SECRET")
google_redirect_uri = _get_secret("GOOGLE_REDIRECT_URI")
google_configured = bool(google_client_id and google_client_secret and google_redirect_uri)

if google_configured and not st.session_state.gcal_access_token:
    code = st.query_params.get("code")
    if code:
        try:
            token_data = exchange_code_for_token(
                google_client_id, google_client_secret, google_redirect_uri, code
            )
            st.session_state.gcal_access_token = token_data.get("access_token")
            st.session_state.gcal_email = get_email(st.session_state.gcal_access_token)
            st.session_state.gcal_connect_error = None
        except Exception as exc:
            st.session_state.gcal_connect_error = str(exc)
        st.query_params.clear()
        st.rerun()

# --------------------------------------------------------------------------
# Sidebar — settings
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")

    plan_date = st.date_input("Plan for", value=dt.date.today())

    day_start_t = st.time_input(
        "Day starts", value=dt.time(9, 0), step=dt.timedelta(minutes=15)
    )
    day_end_t = st.time_input(
        "Day ends", value=dt.time(18, 0), step=dt.timedelta(minutes=15)
    )
    day_start = day_start_t.strftime("%H:%M")
    day_end = day_end_t.strftime("%H:%M")

    morning_end_t = st.time_input(
        "Keep mornings clear until",
        value=dt.time(12, 0),
        step=dt.timedelta(minutes=15),
        help="Flexible tasks won't be scheduled before this time unless "
             "you put a bracket in the task name, e.g. [Make Resume].",
    )
    morning_end = morning_end_t.strftime("%H:%M")

    st.divider()
    st.subheader("Scheduling mode")
    mode_choice = st.radio(
        "How should the plan be generated?",
        ["Rule-based (deterministic, no API needed)", "AI Agent (LLM reasoning)"],
        label_visibility="collapsed",
    )
    force_mode = "llm" if mode_choice.startswith("AI Agent") else "rule-based"

    dev_key = _get_secret("GROQ_API_KEY")
    if force_mode == "llm":
        if dev_key:
            os.environ["GROQ_API_KEY"] = dev_key
            st.caption("✅ AI Agent mode ready.")
        else:
            st.caption(
                "⚠️ No API key configured yet — this will fall back to "
                "rule-based until GROQ_API_KEY is added to Secrets."
            )

    st.divider()
    st.subheader("Google Calendar")
    tz_name = st.text_input("Timezone (IANA)", value="Asia/Amman")

    if st.session_state.gcal_connect_error:
        st.error(f"Connection failed: {st.session_state.gcal_connect_error}")

    if not google_configured:
        st.caption(
            "Not set up yet — add GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / "
            "GOOGLE_REDIRECT_URI to Secrets to enable syncing (see README)."
        )
    elif st.session_state.gcal_access_token:
        who = f" as {st.session_state.gcal_email}" if st.session_state.gcal_email else ""
        st.caption(f"✅ Connected{who}")
        if st.button("Disconnect"):
            st.session_state.gcal_access_token = None
            st.session_state.gcal_email = None
            st.rerun()
    else:
        auth_url = build_auth_url(google_client_id, google_redirect_uri)
        # target="_blank" (not "_top"/"_self") — confirmed via testing that
        # Streamlit Community Cloud's wrapper frame blocks a forced
        # top-level navigation from inside it (target="_top" got silently
        # blocked on click). Opening a new tab only needs the much more
        # commonly-granted "allow-popups" permission, so this works
        # reliably with a normal click. Trade-off: sign-in completes in a
        # separate tab/session — see the README's "known limitation" note.
        st.markdown(
            f'<a href="{auth_url}" target="_blank" style="display:inline-block;'
            f'padding:0.5rem 1rem;background-color:#4285F4;color:white;'
            f'border-radius:0.5rem;text-decoration:none;font-weight:600;">'
            f'🔗 Connect Google Calendar (opens a new tab)</a>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Opens Google's sign-in in a new tab. Once you approve access "
            "there, do the rest of your planning (adding tasks, generating, "
            "accepting) in that new tab — it's a separate session from "
            "this one, so anything added here beforehand won't carry over."
        )

    st.divider()
    if st.button("Load example day"):
        load_example()
        st.rerun()

# --------------------------------------------------------------------------
# Main layout
# --------------------------------------------------------------------------

st.title("🗓️ Planner Agent")
st.caption(
    "Part of a Collector → Triage → **Planner** agent pipeline. Add tasks "
    "and fixed commitments, generate a plan, then Retry for a different "
    "arrangement or Accept to sync it to your Google Calendar."
)

col1, col2 = st.columns(2)

# ---- Fixed commitments -----------------------------------------------
with col1:
    st.subheader("Fixed commitments")

    # Load a whole day's recurring timetable in one click, instead of
    # re-entering the same classes/gym slots every session.
    suggested_day = day_name_for_date(plan_date)
    st.caption(
        f"Load your weekly timetable — {plan_date.strftime('%b %d')} is a "
        f"**{suggested_day}**."
    )
    day_cols = st.columns(len(DAY_ORDER))
    for i, day_name in enumerate(DAY_ORDER):
        is_match = day_name == suggested_day
        if day_cols[i].button(
            day_name[:3],
            key=f"load_day_{day_name}",
            type="primary" if is_match else "secondary",
            use_container_width=True,
            help=f"Load {day_name}'s commitments",
        ):
            st.session_state.commitments = commitments_for(day_name)
            st.session_state.loaded_day = day_name
            st.session_state.result = None
            st.rerun()

    if st.session_state.commitments and st.button(
        "Clear all commitments", key="clear_commitments"
    ):
        st.session_state.commitments = []
        st.session_state.loaded_day = None
        st.rerun()

    with st.form("add_commitment", clear_on_submit=True):
        c_name = st.text_input("Name", key="c_name")
        cc1, cc2 = st.columns(2)
        c_start_t = cc1.time_input(
            "Start", value=dt.time(10, 0), step=dt.timedelta(minutes=15), key="c_start"
        )
        c_end_t = cc2.time_input(
            "End", value=dt.time(11, 0), step=dt.timedelta(minutes=15), key="c_end"
        )
        if st.form_submit_button("Add commitment") and c_name:
            st.session_state.commitments.append(
                {
                    "name": c_name,
                    "start": c_start_t.strftime("%H:%M"),
                    "end": c_end_t.strftime("%H:%M"),
                }
            )

    for i, c in enumerate(st.session_state.commitments):
        row = st.container(border=True)
        rc1, rc2 = row.columns([5, 1])
        rc1.write(f"**{c['name']}** — {c['start']}–{c['end']}")
        if rc2.button("✕", key=f"del_c_{i}"):
            st.session_state.commitments.pop(i)
            st.rerun()

# ---- Tasks --------------------------------------------------------------
with col2:
    st.subheader("Tasks")
    with st.form("add_task", clear_on_submit=True):
        t_name = st.text_input("Name", key="t_name")
        tc1, tc2, tc3 = st.columns(3)
        t_priority = tc1.selectbox("Priority", ["urgent", "important", "low"], key="t_priority")
        t_duration = tc2.number_input("Minutes", min_value=5, max_value=480, value=30, step=5, key="t_duration")
        t_category = tc3.text_input("Category", value="task", key="t_category")
        st.caption(
            "Tip: wrap the name in brackets — [Make Resume] — to allow it "
            "in the morning."
        )
        if st.form_submit_button("Add task") and t_name:
            st.session_state.tasks.append(
                {
                    "name": t_name,
                    "priority": t_priority,
                    "duration_minutes": int(t_duration),
                    "category": t_category,
                }
            )

    for i, t in enumerate(st.session_state.tasks):
        row = st.container(border=True)
        rc1, rc2 = row.columns([5, 1])
        rc1.write(
            f"**{t['name']}** — {t['priority']}, {t['duration_minutes']} min, {t['category']}"
        )
        if rc2.button("✕", key=f"del_t_{i}"):
            st.session_state.tasks.pop(i)
            st.rerun()

st.divider()

# ---- Generate -------------------------------------------------------------

generate = st.button("🚀 Generate plan", type="primary", use_container_width=True)

if generate:
    if not st.session_state.tasks:
        st.warning("Add at least one task first.")
    else:
        tasks = [Task(**t) for t in st.session_state.tasks]
        commitments = [FixedCommitment(**c) for c in st.session_state.commitments]
        with st.spinner("Planning your day..."):
            blocks, mode, error = plan_day(
                tasks, commitments, day_start, day_end,
                force_mode=force_mode, morning_end=morning_end,
            )
        st.session_state.result = blocks
        st.session_state.mode_used = mode
        st.session_state.mode_error = error

# ---- Results ----------------------------------------------------------

if st.session_state.result:
    mode = st.session_state.mode_used
    badge = "🤖 LLM reasoning" if mode == "llm" else "⚙️ Rule-based fallback"
    st.subheader(f"Plan for {plan_date.strftime('%A, %B %d')}")
    st.caption(badge)
    if st.session_state.mode_error:
        st.warning(
            f"AI Agent mode didn't come through, so this is the rule-based "
            f"fallback instead. Reason: {st.session_state.mode_error}"
        )

    # Visual calendar-style timeline
    st.markdown(
        render_calendar_html(
            st.session_state.result, day_start, day_end,
            fixed=st.session_state.commitments,
        ),
        unsafe_allow_html=True,
    )

    unscheduled_blocks = [b for b in st.session_state.result if b.task.startswith("UNSCHEDULED")]
    for b in unscheduled_blocks:
        st.warning(f"⚠️ {b.task}\n\n{b.rationale}")

    with st.expander("Details & rationale"):
        for b in st.session_state.result:
            if b.task.startswith("UNSCHEDULED"):
                continue
            st.markdown(f"**{b.start}–{b.end}**  ·  {b.task}")
            st.caption(b.rationale)

    with st.expander("Raw JSON output"):
        st.code(json.dumps([asdict(b) for b in st.session_state.result], indent=2), language="json")

    st.divider()
    sync_commitments = st.checkbox(
        "Include fixed commitments when syncing to Google Calendar",
        value=True,
        help="Adds your classes, gym, etc. as events too — not just the "
             "agent's task blocks. Turn off if they're already on your "
             "calendar and you don't want duplicates.",
    )
    retry_col, accept_col = st.columns(2)

    with retry_col:
        if st.button("🔁 Retry — get a different schedule", use_container_width=True):
            tasks = [Task(**t) for t in st.session_state.tasks]
            commitments = [FixedCommitment(**c) for c in st.session_state.commitments]
            previous = [b for b in st.session_state.result if not b.task.startswith("UNSCHEDULED")]
            with st.spinner("Generating an alternative..."):
                blocks, mode, error = plan_day(
                    tasks, commitments, day_start, day_end,
                    force_mode=force_mode, previous_blocks=previous,
                    morning_end=morning_end,
                )
            st.session_state.result = blocks
            st.session_state.mode_used = mode
            st.session_state.mode_error = error
            st.rerun()

    with accept_col:
        if not google_configured:
            st.button(
                "✅ Accept & sync to Google Calendar", disabled=True,
                use_container_width=True,
                help="Google Calendar sync isn't set up yet — see README.",
            )
        elif not st.session_state.gcal_access_token:
            st.button(
                "✅ Accept & sync to Google Calendar", disabled=True,
                use_container_width=True,
                help="Connect Google Calendar in the sidebar first.",
            )
        elif st.button("✅ Accept & sync to Google Calendar", type="primary", use_container_width=True):
            scheduled = [b for b in st.session_state.result if not b.task.startswith("UNSCHEDULED")]

            # Build one flat list of (summary, start, end, description) so
            # fixed commitments land on the calendar alongside the agent's
            # blocks — otherwise the synced day has unexplained gaps where
            # classes and gym actually sit.
            to_sync = [
                (b.task, b.start, b.end, b.rationale) for b in scheduled
            ]
            if sync_commitments:
                to_sync += [
                    (c["name"], c["start"], c["end"], "Fixed commitment")
                    for c in st.session_state.commitments
                ]
            to_sync.sort(key=lambda x: x[1])

            created, failed = 0, []
            with st.spinner("Adding events to your Google Calendar..."):
                for summary, s_str, e_str, desc in to_sync:
                    try:
                        start_dt = dt.datetime.combine(
                            plan_date, dt.datetime.strptime(s_str, "%H:%M").time()
                        )
                        end_dt = dt.datetime.combine(
                            plan_date, dt.datetime.strptime(e_str, "%H:%M").time()
                        )
                        # An end at or before the start means the block runs
                        # past midnight into the next calendar day.
                        if end_dt <= start_dt:
                            end_dt += dt.timedelta(days=1)
                        create_event(
                            st.session_state.gcal_access_token,
                            summary=summary,
                            start_dt=start_dt,
                            end_dt=end_dt,
                            timezone=tz_name,
                            description=desc,
                        )
                        created += 1
                    except Exception as exc:
                        failed.append((summary, str(exc)))
            if created:
                st.success(f"Added {created} event(s) to your Google Calendar.")
            if failed:
                st.error(
                    "Some events failed: "
                    + "; ".join(f"{n} ({e})" for n, e in failed)
                )
