"""Create confirmed reservations under a per-resource transaction lock."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from models import BookingResource, Event, OrganizationStore
from store_time import local_to_utc


@dataclass(frozen=True)
class EventTimes:
    start_local: datetime
    end_local: datetime
    timezone_name: str
    start_at: datetime
    end_at: datetime
    reserved_start_at: datetime
    reserved_end_at: datetime
    setup_minutes: int
    cleanup_minutes: int


def prepare_event_times(start_text: str, end_text: str, timezone_name: str,
                        setup_minutes: int, cleanup_minutes: int) -> EventTimes:
    if not timezone_name or len(timezone_name) > 100:
        raise ValueError("Enter a valid venue timezone.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Enter an IANA venue timezone, such as America/Phoenix.") from exc
    try:
        if "T" not in start_text or "T" not in end_text:
            raise ValueError("Enter both service dates and times.")
        start_local = datetime.fromisoformat(start_text)
        end_local = datetime.fromisoformat(end_text)
    except ValueError as exc:
        raise ValueError("Enter both service dates and times.") from exc
    if start_local.tzinfo is not None or end_local.tzinfo is not None:
        raise ValueError("Enter local service times without an offset; choose the venue timezone separately.")
    if not 0 <= setup_minutes <= 1440 or not 0 <= cleanup_minutes <= 1440:
        raise ValueError("Setup and cleanup must each be between 0 and 1440 minutes.")
    start_at = local_to_utc(start_local, zone)
    end_at = local_to_utc(end_local, zone)
    if end_at <= start_at:
        raise ValueError("Service must end after it starts. Use the next date for an overnight event.")
    return EventTimes(start_local, end_local, timezone_name, start_at, end_at,
                      start_at - timedelta(minutes=setup_minutes),
                      end_at + timedelta(minutes=cleanup_minutes),
                      setup_minutes, cleanup_minutes)


def reserve_event(session, organization_id: int, resource_id: int, title: str,
                  times: EventTimes) -> Event:
    """Call within a transaction; PostgreSQL locks the resource until commit.

    Every reservation writer must take this lock before counting overlaps.
    """
    title = title.strip()
    if not title or len(title) > 200:
        raise ValueError("Enter an event name (up to 200 characters).")
    resource = session.execute(
        select(BookingResource)
        .join(OrganizationStore, OrganizationStore.id == BookingResource.organization_store_id)
        .where(BookingResource.id == resource_id,
               OrganizationStore.organization_id == organization_id,
               OrganizationStore.is_active.is_(True), BookingResource.is_enabled.is_(True))
        .with_for_update(of=BookingResource)
    ).scalar_one_or_none()
    if resource is None:
        raise ValueError("That resource is unavailable for this organization.")
    overlapping = session.scalar(select(func.count(Event.id)).where(
        Event.organization_id == organization_id,
        Event.booking_resource_id == resource.id,
        Event.status == "confirmed",
        Event.reserved_start_at < times.reserved_end_at,
        Event.reserved_end_at > times.reserved_start_at,
    ))
    if overlapping >= resource.capacity:
        raise ValueError("This resource has reached its concurrent booking capacity for that time.")
    event = Event(
        organization_id=organization_id, booking_resource_id=resource.id,
        title=title, status="confirmed", venue_timezone_name=times.timezone_name,
        service_start_local=times.start_local, service_end_local=times.end_local,
        service_start_at=times.start_at, service_end_at=times.end_at,
        setup_minutes=times.setup_minutes, cleanup_minutes=times.cleanup_minutes,
        reserved_start_at=times.reserved_start_at, reserved_end_at=times.reserved_end_at,
    )
    session.add(event)
    return event
