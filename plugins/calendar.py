"""
JARVIS Calendar plugin — Google Calendar integration via the Calendar API.

Uses the shared OAuth helper in _google_core.py for authentication.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from _google_core import get_service, credentials_valid
except ImportError:
    get_service = None  # type: ignore[assignment]
    credentials_valid = lambda: False  # type: ignore[assignment]

PLUGIN = {
    "name": "calendar",
    "description": (
        "Google Calendar actions. Use this to view upcoming events, create "
        "events, search for events, or get a daily schedule. "
        "Trigger phrases: 'what's on my calendar', 'schedule for today', "
        "'create a meeting', 'upcoming events', 'what am I doing tomorrow'. "
        "Do NOT use for email — use the gmail tool instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The calendar action to perform: "
                    "'today' (today's schedule), "
                    "'tomorrow' (tomorrow's schedule), "
                    "'week' (next 7 days), "
                    "'upcoming' (next N events), "
                    "'search' (find events by keyword), "
                    "'create' (create a new event), "
                    "'list_calendars' (list accessible calendars)."
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Search keyword for 'search' action.",
            },
            "title": {
                "type": "STRING",
                "description": "Event title for 'create' action.",
            },
            "date": {
                "type": "STRING",
                "description": (
                    "Date for 'create' action in YYYY-MM-DD format. "
                    "Defaults to today if omitted."
                ),
            },
            "time": {
                "type": "STRING",
                "description": (
                    "Start time for 'create' action in HH:MM (24h) format. "
                    "Defaults to the next whole hour if omitted."
                ),
            },
            "duration": {
                "type": "STRING",
                "description": (
                    "Duration in minutes for 'create' action (default: 60)."
                ),
            },
            "count": {
                "type": "STRING",
                "description": "Number of events for 'upcoming' action (default: 5).",
            },
        },
        "required": ["action"],
    },
}


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _parse_date(s: str | None, default: datetime | None = None) -> datetime:
    d = default or _now_local()
    if not s:
        return d
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").replace(
            tzinfo=d.tzinfo
        )
    except ValueError:
        return d


def _parse_time(s: str | None, base: datetime | None = None) -> datetime:
    b = base or _now_local()
    if not s:
        # default: next whole hour
        nxt = b.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return nxt
    try:
        t = datetime.strptime(s.strip(), "%H:%M")
        return b.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    except ValueError:
        return b.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _fmt_event(ev: dict) -> str:
    """Format a single calendar event for display."""
    summary = ev.get("summary", "(no title)")
    start = ev.get("start", {})
    end = ev.get("end", {})

    if "dateTime" in start:
        st = start["dateTime"]
        try:
            dt = datetime.fromisoformat(st)
            time_str = dt.strftime("%H:%M")
        except Exception:
            time_str = st
    elif "date" in start:
        time_str = "all day"
    else:
        time_str = "?"

    if "dateTime" in end:
        et = end["dateTime"]
        try:
            dt_e = datetime.fromisoformat(et)
            end_str = dt_e.strftime("%H:%M")
        except Exception:
            end_str = et
    else:
        end_str = ""

    loc = ev.get("location", "")
    loc_str = f" @ {loc}" if loc else ""

    if time_str == "all day":
        return f"  • {summary} (all day){loc_str}"
    return f"  • {summary} — {time_str}" + (f"–{end_str}" if end_str else "") + loc_str


def _handle_today(service) -> str:
    now = _now_local()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = _fetch_events(service, start.isoformat(), end.isoformat(), max_results=20)
    if not events:
        return "Your calendar is clear today — nothing scheduled."
    header = f"Today's schedule ({now.strftime('%A %B %d')}):"
    return header + "\n" + "\n".join(_fmt_event(e) for e in events)


def _handle_tomorrow(service) -> str:
    now = _now_local()
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    events = _fetch_events(service, start.isoformat(), end.isoformat(), max_results=20)
    if not events:
        return "Tomorrow looks clear — no events scheduled."
    header = f"Tomorrow's schedule ({start.strftime('%A %B %d')}):"
    return header + "\n" + "\n".join(_fmt_event(e) for e in events)


def _handle_week(service) -> str:
    now = _now_local()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    events = _fetch_events(service, start.isoformat(), end.isoformat(), max_results=30)
    if not events:
        return "Nothing on your calendar for the next 7 days."
    header = f"Next 7 days ({now.strftime('%b %d')} – {end.strftime('%b %d')}):"
    return header + "\n" + "\n".join(_fmt_event(e) for e in events)


def _handle_upcoming(service, count: int) -> str:
    now = _now_local()
    events = _fetch_events(
        service,
        now.isoformat(),
        (now + timedelta(days=30)).isoformat(),
        max_results=count,
    )
    if not events:
        return "No upcoming events found."
    header = f"Next {len(events)} upcoming event(s):"
    return header + "\n" + "\n".join(_fmt_event(e) for e in events)


def _handle_search(service, query: str) -> str:
    now = _now_local()
    events = _fetch_events(
        service,
        now.isoformat(),
        (now + timedelta(days=90)).isoformat(),
        max_results=10,
        query=query,
    )
    if not events:
        return f"No events found matching '{query}'."
    header = f"Events matching '{query}':"
    return header + "\n" + "\n".join(_fmt_event(e) for e in events)


def _handle_create(service, title: str, date_str: str, time_str: str, duration_min: int) -> str:
    base = _parse_date(date_str)
    start = _parse_time(time_str, base)
    end = start + timedelta(minutes=duration_min)

    body = {
        "summary": title,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }

    try:
        created = service.events().insert(calendarId="primary", body=body).execute()
        link = created.get("htmlLink", "")
        return (
            f"Created event '{title}' on {start.strftime('%B %d at %H:%M')} "
            f"({duration_min} minutes)."
            + (f"\n{link}" if link else "")
        )
    except Exception as e:
        return f"Failed to create event: {e}"


def _handle_list_calendars(service) -> str:
    try:
        calendar_list = service.calendarList().list().execute().get("items", [])
        if not calendar_list:
            return "No calendars found."
        lines = ["Your calendars:"]
        for cal in calendar_list:
            primary = " (primary)" if cal.get("primary") else ""
            lines.append(f"  • {cal.get('summary', cal['id'])}{primary}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list calendars: {e}"


def _fetch_events(service, time_min: str, time_max: str,
                   max_results: int = 10, query: str = "") -> list[dict]:
    kwargs = {
        "calendarId": "primary",
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if query:
        kwargs["q"] = query
    try:
        return service.events().list(**kwargs).execute().get("items", [])
    except Exception:
        return []


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()

    if not action:
        return "Please specify an action: today, tomorrow, week, upcoming, search, create, or list_calendars."

    if get_service is None:
        return "Google Calendar integration is not available. The _google_core module could not be imported."

    if not credentials_valid():
        return (
            "Google Calendar access is not set up yet. "
            "Please authorise Gmail first (the same credentials are used for Calendar)."
        )

    try:
        service = get_service()
    except Exception as e:
        return f"Could not connect to Google Calendar: {e}"

    if action == "today":
        return _handle_today(service)
    elif action == "tomorrow":
        return _handle_tomorrow(service)
    elif action == "week":
        return _handle_week(service)
    elif action == "upcoming":
        try:
            count = int(str(parameters.get("count", "5")).strip())
        except (ValueError, TypeError):
            count = 5
        return _handle_upcoming(service, max(1, min(20, count)))
    elif action == "search":
        query = (parameters.get("query") or "").strip()
        if not query:
            return "Please provide a search keyword."
        return _handle_search(service, query)
    elif action == "create":
        title = (parameters.get("title") or "").strip()
        if not title:
            return "Please provide an event title."
        date_str = (parameters.get("date") or "").strip()
        time_str = (parameters.get("time") or "").strip()
        try:
            duration = int(str(parameters.get("duration", "60")).strip())
        except (ValueError, TypeError):
            duration = 60
        return _handle_create(service, title, date_str, time_str, max(5, min(480, duration)))
    elif action == "list_calendars":
        return _handle_list_calendars(service)
    else:
        return f"Unknown calendar action: '{action}'. Use today, tomorrow, week, upcoming, search, create, or list_calendars."
