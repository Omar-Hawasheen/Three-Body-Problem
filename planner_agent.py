"""
Planner Agent
-------------
Takes a list of triaged tasks + fixed commitments for a day and produces a
structured daily schedule with a one-line rationale per block.

Two modes:
  1. LLM mode (default, if GROQ_API_KEY is set) — asks an LLM to reason about
     sequencing (deep work vs. quick tasks, energy levels, buffers) and
     return a strict JSON schedule. The output is validated; if it's invalid
     (overlaps a fixed commitment, goes outside the work day, etc.) the
     agent sends the error back to the LLM once and asks it to fix it.
  2. Rule-based fallback (no API key needed) — a simple greedy scheduler so
     the whole pipeline runs and is demoable even with zero setup.

This is the "Planner Agent" stage of a larger 3-agent pipeline
(Collector -> Triage -> Planner). It only needs the triaged task list and
the day's fixed commitments as input — see example_input.json.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class Task:
    name: str
    priority: str          # "urgent" | "important" | "low"
    duration_minutes: int
    category: str          # e.g. "study", "project", "errand", "email"


@dataclass
class FixedCommitment:
    name: str
    start: str              # "HH:MM"
    end: str                # "HH:MM"


@dataclass
class ScheduleBlock:
    start: str
    end: str
    task: str
    rationale: str


PRIORITY_ORDER = {"urgent": 0, "important": 1, "low": 2}

# Flexible tasks are kept out of the morning by default — mornings are
# reserved unless a task is explicitly opted in. A task opts in by putting
# a bracket in its name: either wrapping the whole name, "[Make Resume]",
# or tagging it, "Make Resume [morning]". Anything in square brackets or
# parentheses counts.
DEFAULT_MORNING_END = "12:00"

# Breathing room inserted between two back-to-back flexible tasks. This is
# never inserted between a task and a fixed commitment — only task-to-task.
BREAK_MINUTES = 5

_BRACKET_RE = re.compile(r"[\[\(][^\]\)]*[\]\)]")


def allows_morning(task_name: str) -> bool:
    """True if this task may be scheduled before the morning cutoff.

    The opt-in marker is a bracket anywhere in the task name — so
    "[Make Resume]", "Make Resume [morning]" and "Make Resume (am)" all
    qualify, while a plain "Make Resume" does not."""
    return bool(_BRACKET_RE.search(task_name))


def strip_brackets(task_name: str) -> str:
    """Task name with any bracketed marker removed, for display. Keeps a
    name that is entirely bracketed intact rather than returning nothing."""
    cleaned = _BRACKET_RE.sub("", task_name).strip(" -–—,")
    return cleaned or task_name.strip("[]() ")


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def _to_dt(hhmm: str) -> datetime:
    return datetime.strptime(hhmm, "%H:%M")


def _to_hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _day_bounds(day_start: str, day_end: str) -> tuple[datetime, datetime]:
    """Parses the work day into datetimes. If day_end is at or before
    day_start (e.g. day_end="00:00" meaning midnight), it's treated as
    continuing into the next calendar day rather than an invalid/negative
    window. strftime("%H:%M") on the resulting datetimes still displays
    correctly regardless of which day they landed on, so nothing downstream
    needs to know this happened."""
    start_dt = _to_dt(day_start)
    end_dt = _to_dt(day_end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


# --------------------------------------------------------------------------
# Validation (the "self-check" step)
# --------------------------------------------------------------------------

def validate_schedule(
    blocks: list[ScheduleBlock],
    fixed: list[FixedCommitment],
    day_start: str,
    day_end: str,
) -> list[str]:
    """Returns a list of problems found. Empty list = valid schedule."""
    problems = []
    day_start_dt, day_end_dt = _day_bounds(day_start, day_end)

    parsed = []
    for b in blocks:
        try:
            s, e = _to_dt(b.start), _to_dt(b.end)
        except ValueError:
            problems.append(f"Block '{b.task}' has an unparseable time.")
            continue
        if s >= e:
            problems.append(f"Block '{b.task}' ends before it starts.")
            continue
        if s < day_start_dt or e > day_end_dt:
            problems.append(
                f"Block '{b.task}' ({b.start}-{b.end}) falls outside the "
                f"work day ({day_start}-{day_end})."
            )
        parsed.append((s, e, b.task))

    # check overlap with fixed commitments
    for s, e, name in parsed:
        for fc in fixed:
            fs, fe = _to_dt(fc.start), _to_dt(fc.end)
            if _overlaps(s, e, fs, fe):
                problems.append(
                    f"Block '{name}' ({_to_hhmm(s)}-{_to_hhmm(e)}) overlaps "
                    f"fixed commitment '{fc.name}' ({fc.start}-{fc.end})."
                )

    # check overlap between scheduled blocks themselves
    parsed_sorted = sorted(parsed, key=lambda x: x[0])
    for i in range(len(parsed_sorted) - 1):
        s1, e1, n1 = parsed_sorted[i]
        s2, e2, n2 = parsed_sorted[i + 1]
        if e1 > s2:
            problems.append(f"Blocks '{n1}' and '{n2}' overlap each other.")

    return problems


# --------------------------------------------------------------------------
# Rule-based fallback scheduler (no API key required)
# --------------------------------------------------------------------------

def rule_based_plan(
    tasks: list[Task],
    fixed: list[FixedCommitment],
    day_start: str,
    day_end: str,
    randomize: bool = False,
    morning_end: str = DEFAULT_MORNING_END,
) -> list[ScheduleBlock]:
    """Greedy scheduler: urgent first, longest deep-work tasks in the first
    long stretch available, short tasks batched together, working around
    fixed commitments. No LLM required.

    Mornings (before morning_end) are left clear for flexible tasks unless
    a task opts in by putting a bracket in its name — see allows_morning().
    A BREAK_MINUTES gap is left between consecutive tasks.

    randomize: when True, ties within the same priority tier are shuffled
    instead of always breaking the same way — used for the "Retry" button
    so a second click can produce a genuinely different valid schedule
    instead of the identical one."""

    day_start_dt, day_end_dt = _day_bounds(day_start, day_end)

    # free windows = work day minus fixed commitments
    busy = sorted(
        [(_to_dt(fc.start), _to_dt(fc.end)) for fc in fixed], key=lambda x: x[0]
    )
    free_windows = []
    cursor = day_start_dt
    for fs, fe in busy:
        if fs > cursor:
            free_windows.append((cursor, fs))
        cursor = max(cursor, fe)
    if cursor < day_end_dt:
        free_windows.append((cursor, day_end_dt))

    # sort tasks: urgent > important > low, and within a tier, longer
    # (deep-work) tasks first so they land in the freshest window — unless
    # randomize is set, in which case tier order is kept but the order
    # within each tier is shuffled instead of sorted by duration.
    if randomize:
        tiers: dict[tuple[int, int], list[Task]] = {}
        for t in tasks:
            key = (0 if allows_morning(t.name) else 1,
                   PRIORITY_ORDER.get(t.priority, 3))
            tiers.setdefault(key, []).append(t)
        ordered = []
        for tier in sorted(tiers):
            group = tiers[tier][:]
            random.shuffle(group)
            ordered.extend(group)
    else:
        ordered = sorted(
            tasks,
            key=lambda t: (
                # Bracketed tasks go first so they actually occupy the
                # morning they were opted into, rather than being permitted
                # there but pushed later by priority ordering.
                0 if allows_morning(t.name) else 1,
                PRIORITY_ORDER.get(t.priority, 3),
                -t.duration_minutes,
            ),
        )

    blocks: list[ScheduleBlock] = []
    unscheduled: list[Task] = []

    morning_end_dt = _to_dt(morning_end)
    if morning_end_dt <= day_start_dt:
        # A cutoff at or before the day's start means "no morning guard".
        morning_end_dt = day_start_dt

    # Tracks the end of the last task placed in each window, so the 5-minute
    # break is only applied task-to-task and never eats into the start of a
    # window that begins right after a fixed commitment.
    last_task_end: dict[int, datetime] = {}

    def _try_place(task: Task, respect_morning: bool):
        """Finds the first window that fits. Returns (index, start, end) or
        None. When respect_morning is set, slots before the cutoff are
        skipped unless the task's name opts in with a bracket."""
        needed = timedelta(minutes=task.duration_minutes)
        guard = respect_morning and not allows_morning(task.name)
        for i, (ws, we) in enumerate(free_windows):
            candidate = ws
            # 5-minute breather after a previous task in this same window
            if i in last_task_end and candidate == last_task_end[i]:
                candidate = candidate + timedelta(minutes=BREAK_MINUTES)
            if guard and candidate < morning_end_dt:
                candidate = max(candidate, morning_end_dt)
            if we - candidate >= needed:
                return i, candidate, candidate + needed
        return None

    for task in ordered:
        # First pass keeps mornings clear; if the task simply cannot fit in
        # the rest of the day, a second pass allows the morning rather than
        # dropping the task entirely.
        spot = _try_place(task, respect_morning=True)
        pushed_to_morning = False
        if spot is None:
            spot = _try_place(task, respect_morning=False)
            pushed_to_morning = spot is not None

        if spot is None:
            unscheduled.append(task)
            continue

        i, block_start, block_end = spot
        rationale = _rationale_for(
            task, block_start, morning_end_dt, pushed_to_morning
        )
        blocks.append(
            ScheduleBlock(
                start=_to_hhmm(block_start),
                end=_to_hhmm(block_end),
                task=task.name,
                rationale=rationale,
            )
        )
        ws, we = free_windows[i]
        free_windows[i] = (block_end, we)
        last_task_end[i] = block_end

    if unscheduled:
        names = ", ".join(t.name for t in unscheduled)
        blocks.append(
            ScheduleBlock(
                start="--:--",
                end="--:--",
                task=f"UNSCHEDULED: {names}",
                rationale="Didn't fit in remaining free time today — "
                           "consider moving to tomorrow or shortening scope.",
            )
        )

    return sorted(blocks, key=lambda b: b.start)


def _rationale_for(
    task: Task,
    block_start: datetime,
    morning_end_dt: datetime | None = None,
    pushed_to_morning: bool = False,
) -> str:
    if pushed_to_morning:
        return (
            "Placed in the morning as a last resort — nothing later in the "
            "day was free. Bracket the name to allow this without the warning."
        )
    if allows_morning(task.name) and morning_end_dt and block_start < morning_end_dt:
        return "Bracketed, so it was allowed into the morning."
    if task.duration_minutes >= 90:
        return (
            f"Scheduled in the first long free stretch after the morning "
            f"since it's a {task.duration_minutes}-min deep-work item."
        )
    if task.priority == "urgent":
        return "Marked urgent, so placed as early as it would fit."
    if task.duration_minutes <= 20:
        return "Short task — batched into a lighter slot."
    return "Fit into an available window based on priority and length."


# --------------------------------------------------------------------------
# LLM mode (Groq) with one self-correction retry
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a scheduling agent. Given a list of tasks (with \
priority, duration, category) and a list of fixed commitments for the day, \
produce a JSON schedule.

Rules:
- Do not schedule anything during a fixed commitment.
- Do not schedule anything outside the given work day window.
- MORNING RULE: do not schedule a task before "morning_end" unless its \
  name contains a bracket, e.g. "[Make Resume]" or "Make Resume [morning]". \
  Bracketed tasks may go in the morning. Everything else waits until after \
  morning_end. Only break this rule if a task has nowhere else to fit, and \
  say so in its rationale if you do.
- Leave a 5-minute gap between two consecutive tasks. No gap is needed \
  between a task and a fixed commitment.
- Put long deep-work tasks (>= 60 min) in the first long uninterrupted \
  stretch available after morning_end, when focus is freshest.
- Batch short tasks (<= 20 min) together rather than scattering them, \
  still with the 5-minute gap between them.
- Respect priority: urgent > important > low.
- If something genuinely does not fit, include it in "unscheduled" instead \
  of forcing an overlap.
- Give a short one-sentence rationale for each scheduled block.

Return ONLY valid JSON, no prose, no markdown fences, in this exact shape:
{
  "blocks": [
    {"start": "HH:MM", "end": "HH:MM", "task": "...", "rationale": "..."}
  ],
  "unscheduled": ["task name", ...]
}
"""


# Groq deprecates/renames models periodically. Override with the
# GROQ_MODEL env var (or a Streamlit secret of the same name) if this one
# gets retired again — check https://console.groq.com/docs/models for the
# current list.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _call_groq(messages: list[dict], api_key: str, temperature: float = 0.2) -> str:
    import urllib.request

    model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Groq's API sits behind Cloudflare, which blocks requests
            # with no User-Agent (returns a 403). Python's urllib sends
            # none by default, so we set one explicitly.
            "User-Agent": "planner-agent/1.0 (+https://github.com/)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def llm_plan(
    tasks: list[Task],
    fixed: list[FixedCommitment],
    day_start: str,
    day_end: str,
    api_key: str,
    previous_blocks: Optional[list[ScheduleBlock]] = None,
    morning_end: str = DEFAULT_MORNING_END,
) -> list[ScheduleBlock]:
    """previous_blocks: pass the last schedule shown to the user (from a
    "Retry" click) to explicitly ask the model for a different, still-valid
    arrangement instead of repeating the same one."""
    user_prompt = json.dumps(
        {
            "day_start": day_start,
            "day_end": day_end,
            "morning_end": morning_end,
            "break_minutes": BREAK_MINUTES,
            "fixed_commitments": [asdict(f) for f in fixed],
            "tasks": [asdict(t) for t in tasks],
        },
        indent=2,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    temperature = 0.2
    if previous_blocks:
        temperature = 0.7  # encourage a genuinely different arrangement
        messages.append(
            {
                "role": "user",
                "content": (
                    "The user asked to retry — give a different valid "
                    "schedule than this previous one, still following all "
                    "the rules:\n"
                    + json.dumps([asdict(b) for b in previous_blocks], indent=2)
                ),
            }
        )

    raw = _call_groq(messages, api_key, temperature=temperature)
    blocks, unscheduled = _parse_llm_json(raw)
    problems = validate_schedule(blocks, fixed, day_start, day_end)

    if problems:
        # one self-correction pass: hand the errors back to the LLM
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": (
                    "That schedule has problems, fix them and return the "
                    "corrected JSON in the same format only:\n- "
                    + "\n- ".join(problems)
                ),
            }
        )
        raw2 = _call_groq(messages, api_key)
        blocks2, unscheduled2 = _parse_llm_json(raw2)
        problems2 = validate_schedule(blocks2, fixed, day_start, day_end)
        if not problems2:
            blocks, unscheduled = blocks2, unscheduled2
        # if still broken after one retry, fall through and return the
        # first attempt anyway — caller can see remaining problems via
        # validate_schedule() again if desired

    for name in unscheduled:
        blocks.append(
            ScheduleBlock(
                start="--:--",
                end="--:--",
                task=f"UNSCHEDULED: {name}",
                rationale="Model flagged this as not fitting today.",
            )
        )

    return sorted(blocks, key=lambda b: b.start)


def _parse_llm_json(raw: str) -> tuple[list[ScheduleBlock], list[str]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    data = json.loads(cleaned)
    blocks = [
        ScheduleBlock(
            start=b["start"], end=b["end"], task=b["task"], rationale=b["rationale"]
        )
        for b in data.get("blocks", [])
    ]
    unscheduled = data.get("unscheduled", [])
    return blocks, unscheduled


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def plan_day(
    tasks: list[Task],
    fixed: list[FixedCommitment],
    day_start: str = "09:00",
    day_end: str = "18:00",
    force_mode: Optional[str] = None,
    previous_blocks: Optional[list[ScheduleBlock]] = None,
    morning_end: str = DEFAULT_MORNING_END,
) -> tuple[list[ScheduleBlock], str, Optional[str]]:
    """Returns (blocks, mode_used, error). mode_used is 'llm' or
    'rule-based'. error is None on success, or a short message describing
    why the LLM path failed (if it did) even though a rule-based schedule
    is still returned so the caller always has something usable.

    force_mode: None (auto-detect based on GROQ_API_KEY presence, the
    original CLI behavior), "llm" (require the LLM path), or "rule-based"
    (skip the LLM entirely, e.g. when the user has explicitly chosen the
    free deterministic mode in the UI).

    previous_blocks: pass the previously shown schedule to get a different
    valid arrangement back instead of the same one — used for "Retry".
    """
    api_key = os.environ.get("GROQ_API_KEY")

    if force_mode == "rule-based":
        blocks = rule_based_plan(
            tasks, fixed, day_start, day_end,
            randomize=bool(previous_blocks), morning_end=morning_end,
        )
        return blocks, "rule-based", None

    if force_mode == "llm":
        if not api_key:
            blocks = rule_based_plan(
                tasks, fixed, day_start, day_end, morning_end=morning_end
            )
            return blocks, "rule-based", "No GROQ_API_KEY is configured."
        try:
            blocks = llm_plan(
                tasks, fixed, day_start, day_end, api_key,
                previous_blocks=previous_blocks, morning_end=morning_end,
            )
            return blocks, "llm", None
        except Exception as exc:
            blocks = rule_based_plan(
                tasks, fixed, day_start, day_end, morning_end=morning_end
            )
            return blocks, "rule-based", str(exc)

    # auto-detect (used by the CLI)
    if api_key:
        try:
            blocks = llm_plan(
                tasks, fixed, day_start, day_end, api_key,
                morning_end=morning_end,
            )
            return blocks, "llm", None
        except Exception as exc:  # network/parsing failure -> fall back
            print(f"[planner_agent] LLM mode failed ({exc}), "
                  f"falling back to rule-based scheduler.", file=sys.stderr)
    return (
        rule_based_plan(tasks, fixed, day_start, day_end, morning_end=morning_end),
        "rule-based",
        None,
    )


def render_markdown(blocks: list[ScheduleBlock], mode: str) -> str:
    lines = [f"# Daily Plan (generated by: {mode} mode)\n"]
    for b in blocks:
        lines.append(f"**{b.start}–{b.end}**  {b.task}")
        lines.append(f"> {b.rationale}\n")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python planner_agent.py <input.json> [day_start] [day_end]")
        sys.exit(1)

    input_path = sys.argv[1]
    day_start = sys.argv[2] if len(sys.argv) > 2 else "09:00"
    day_end = sys.argv[3] if len(sys.argv) > 3 else "18:00"

    with open(input_path) as f:
        raw = json.load(f)

    tasks = [Task(**t) for t in raw["tasks"]]
    fixed = [FixedCommitment(**f) for f in raw.get("fixed_commitments", [])]

    blocks, mode, error = plan_day(tasks, fixed, day_start, day_end)
    if error:
        print(f"[planner_agent] note: {error}", file=sys.stderr)

    md = render_markdown(blocks, mode)
    print(md)

    out_json = "plan_output.json"
    out_md = "plan_output.md"
    with open(out_json, "w") as f:
        json.dump([asdict(b) for b in blocks], f, indent=2)
    with open(out_md, "w") as f:
        f.write(md)

    print(f"\n(saved {out_json} and {out_md}, mode: {mode})", file=sys.stderr)


if __name__ == "__main__":
    main()
