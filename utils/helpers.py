"""Small shared helpers: dates, formatting and severity styling."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

SEVERITY_ORDER = {"high": 0, "moderate": 1, "informational": 2, "none": 3}

SEVERITY_LABEL = {
    "high": "High concern",
    "moderate": "Moderate concern",
    "informational": "Information available",
    "none": "No known interaction found in the checked sources",
}

SEVERITY_COLOR = {
    "high": "#d64545",
    "moderate": "#e08b2f",
    "informational": "#2b8ca8",
    "none": "#2f9e6e",
}

SEVERITY_ICON = {"high": "🔴", "moderate": "🟠", "informational": "🔵", "none": "🟢"}

# Clinical tier names used in pharmacy references, shown alongside the plain-language label.
SEVERITY_TIER = {
    "high": "Major",
    "moderate": "Moderate",
    "informational": "Minor",
    "none": "No match found",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat(sep=" ")


def today_iso() -> str:
    return date.today().isoformat()


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def pretty_datetime(value) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(value)


def pretty_date(value) -> str:
    d = parse_date(value)
    return d.strftime("%d %b %Y") if d else "-"


def date_range(start: date, days: int):
    return [start + timedelta(days=i) for i in range(max(int(days or 0), 0))]


def severity_sort_key(severity) -> int:
    return SEVERITY_ORDER.get(str(severity or "none").lower(), 9)


def severity_label(severity) -> str:
    return SEVERITY_LABEL.get(str(severity or "none").lower(), "Unclassified")


def severity_tier(severity) -> str:
    """Clinical tier name (Major / Moderate / Minor) for the given severity."""
    return SEVERITY_TIER.get(str(severity or "none").lower(), "Unclassified")


def severity_full(severity) -> str:
    """e.g. 'High concern (Major)'."""
    key = str(severity or "none").lower()
    if key == "none":
        return SEVERITY_LABEL.get(key, "Unclassified")
    return "%s (%s)" % (SEVERITY_LABEL.get(key, key), SEVERITY_TIER.get(key, key))


def safe_percent(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def titlecase_drug(name: str) -> str:
    return " ".join(w.capitalize() for w in str(name or "").split())
