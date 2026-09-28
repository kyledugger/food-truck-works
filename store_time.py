"""Convert store wall times and legacy UTC-naive database instants."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def local_to_utc(value: datetime, zone: ZoneInfo) -> datetime:
    """Return an aware UTC instant; reject DST gaps and repeated wall times."""
    if value.tzinfo is not None:
        raise ValueError("Expected a local date and time without an offset")
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError("This time is missing or occurs twice in the store timezone")
    return candidates.pop()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize old/SQLite UTC-naive values and PostgreSQL aware values."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utc_iso(value: datetime | None) -> str | None:
    """Serialize a UTC instant for browsers, including legacy UTC-naive rows."""
    if value is None:
        return None
    return as_utc(value).isoformat().replace("+00:00", "Z")


def local_day_bounds(day, zone: ZoneInfo):
    start = local_to_utc(datetime.combine(day, datetime.min.time()), zone)
    end = local_to_utc(datetime.combine(day + timedelta(days=1), datetime.min.time()), zone)
    return start, end
