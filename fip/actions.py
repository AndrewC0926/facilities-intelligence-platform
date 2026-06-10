"""
Actions / age logic — the deterministic, testable half of the workflow layer.

`vw_open_actions` lists the open items; this module owns the time-relative logic
(how old is an action, what colour band is it in) and the small summary the exec
brief needs. Age is relative to "today", so every function takes an injectable
`today` — the dashboard passes the real date, the tests pass a fixed one, and the
seed stays deterministic.

Age bands:  green < 30 days · yellow 30-60 days · red > 60 days.
"""
import datetime

from fip import db

GREEN_MAX = 30   # < 30 days  -> green
YELLOW_MAX = 60  # 30-60 days -> yellow; > 60 -> red


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def age_days(created_at, today=None):
    """Whole days between created_at and today (>= 0)."""
    created = _as_date(created_at)
    if created is None:
        return None
    today = _as_date(today) or datetime.date.today()
    return (today - created).days


def band(days):
    """Map an age in days to a colour band."""
    if days is None:
        return "unknown"
    if days < GREEN_MAX:
        return "green"
    if days <= YELLOW_MAX:
        return "yellow"
    return "red"


def open_actions(conn, today=None):
    """The open/in_progress actions (from vw_open_actions) with age_days and band
    attached, oldest first."""
    rows = db.query(conn, "SELECT * FROM vw_open_actions")
    out = []
    for r in rows:
        r = dict(r)
        r["age_days"] = age_days(r["created_at"], today)
        r["age_band"] = band(r["age_days"])
        out.append(r)
    out.sort(key=lambda r: (r["age_days"] is None, -(r["age_days"] or 0)))
    return out


def summary(conn, today=None):
    """Small roll-up for the exec brief: how many open, and the oldest one's age."""
    rows = open_actions(conn, today)
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]
    oldest = max(ages) if ages else None
    oldest_row = None
    if oldest is not None:
        oldest_row = next(r for r in rows if r["age_days"] == oldest)
    return {
        "open_count": len(rows),
        "oldest_age_days": oldest,
        "oldest_title": oldest_row["title"] if oldest_row else None,
        "oldest_site": oldest_row["site_name"] if oldest_row else None,
    }
