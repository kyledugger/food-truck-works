"""Minute-aligned, store-local claim windows. Intervals are [start, end)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from store_time import local_day_bounds, local_to_utc, as_utc


def minute_floor(value):
    return as_utc(value).replace(second=0, microsecond=0)


def minute_ceiling(value):
    value = as_utc(value)
    floor = minute_floor(value)
    return floor if value == floor else floor + timedelta(minutes=1)


def window_bounds(launched, zone_name, day, manager, last_end=None, activation=None):
    launched = minute_floor(launched)
    zone = ZoneInfo(zone_name)
    today = launched.astimezone(zone).date()
    if day > today or (not manager and day not in {today, today - timedelta(days=1)}):
        raise ValueError("Choose today or yesterday." if not manager else "Choose today or an earlier date.")
    day_start, day_end = local_day_bounds(day, zone)
    end = launched if day in {today, today - timedelta(days=1)} else day_end
    lower = max(day_start, end - timedelta(hours=24))
    if last_end is not None:
        last_end = minute_ceiling(last_end)
        if end - timedelta(hours=24) <= last_end <= end:
            lower = max(lower, last_end)
    if activation is not None:
        lower = max(lower, minute_ceiling(activation))
    return lower, end


def parse_start(day, clock, zone_name):
    value = datetime.fromisoformat(f"{day.isoformat()}T{clock}")
    if value.tzinfo is not None or value.second or value.microsecond or len(clock) != 5:
        raise ValueError("Enter a time in hours and minutes.")
    return local_to_utc(value, ZoneInfo(zone_name))


def validate_window(start, end, lower, upper):
    if any(value != minute_floor(value) for value in (start, end)):
        raise ValueError("Claim boundaries must use whole minutes.")
    if not lower <= start < end <= upper or end - start > timedelta(hours=24):
        raise ValueError("Choose a start after the last claim and before the end, within 24 hours.")
