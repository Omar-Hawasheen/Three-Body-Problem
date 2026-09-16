"""
Weekly schedule
---------------
Your recurring weekly timetable, so you don't have to re-enter fixed
commitments every time. Pick a day in the UI and its commitments load
straight into the planner.

Times are 24-hour "HH:MM" strings — the same format the rest of the app
uses. Edit this file to change your timetable; nothing else needs to know.

DAY_ORDER starts on Sunday to match the week layout used here.
"""

from __future__ import annotations

import datetime as dt

DAY_ORDER = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]

# name, start, end
WEEKLY_SCHEDULE: dict[str, list[dict[str, str]]] = {
    "Sunday": [
        {"name": "Study", "start": "07:25", "end": "08:25"},
        {"name": "Database Systems", "start": "09:00", "end": "10:00"},
        {"name": "Data Visualization", "start": "12:00", "end": "13:00"},
        {"name": "Study", "start": "13:00", "end": "16:00"},
        {"name": "Entrepreneurship", "start": "16:00", "end": "17:00"},
        {"name": "Gym", "start": "17:00", "end": "18:30"},
    ],
    "Monday": [
        {"name": "Study", "start": "07:55", "end": "08:55"},
        {"name": "Algorithms Design and Analysis", "start": "09:30", "end": "11:00"},
        {"name": "Machine Learning", "start": "12:30", "end": "14:00"},
        {"name": "Database Systems Lab (online)", "start": "14:00", "end": "17:00"},
        {"name": "Study", "start": "17:00", "end": "18:00"},
        {"name": "Gym", "start": "18:30", "end": "20:00"},
    ],
    "Tuesday": [
        {"name": "Study", "start": "07:25", "end": "08:25"},
        {"name": "Database Systems", "start": "09:00", "end": "10:00"},
        {"name": "Data Visualization", "start": "12:00", "end": "13:00"},
        {"name": "Study", "start": "13:00", "end": "16:00"},
        {"name": "Entrepreneurship", "start": "16:00", "end": "17:00"},
        {"name": "Gym", "start": "17:00", "end": "18:30"},
    ],
    "Wednesday": [
        {"name": "Study", "start": "07:55", "end": "08:55"},
        {"name": "Algorithms Design and Analysis", "start": "09:30", "end": "11:00"},
        {"name": "Machine Learning", "start": "12:30", "end": "14:00"},
        {"name": "Study", "start": "15:00", "end": "17:00"},
        {"name": "Badminton", "start": "19:00", "end": "20:30"},
    ],
    "Thursday": [
        {"name": "Study", "start": "07:25", "end": "08:25"},
        {"name": "Database Systems", "start": "09:00", "end": "10:00"},
        {"name": "Data Visualization", "start": "12:00", "end": "13:00"},
        {"name": "Study", "start": "13:00", "end": "16:00"},
        {"name": "Entrepreneurship", "start": "16:00", "end": "17:00"},
    ],
    "Friday": [],
    "Saturday": [
        {"name": "Gym", "start": "17:00", "end": "18:30"},
    ],
}


def commitments_for(day_name: str) -> list[dict[str, str]]:
    """Returns a fresh copy of that day's commitments (copied so the UI can
    edit/delete entries without mutating the template)."""
    return [dict(c) for c in WEEKLY_SCHEDULE.get(day_name, [])]


def day_name_for_date(date: dt.date) -> str:
    """Maps a calendar date to its day name, so the UI can preselect the
    day matching whatever date the user is planning for."""
    # Python's weekday(): Monday=0 ... Sunday=6
    return ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"][date.weekday()]


def earliest_start(day_name: str) -> str | None:
    """Earliest commitment start for a day, or None if the day is free.
    Used to auto-widen the planning window so commitments aren't cut off."""
    items = WEEKLY_SCHEDULE.get(day_name, [])
    return min((c["start"] for c in items), default=None)


def latest_end(day_name: str) -> str | None:
    """Latest commitment end for a day, or None if the day is free."""
    items = WEEKLY_SCHEDULE.get(day_name, [])
    return max((c["end"] for c in items), default=None)
