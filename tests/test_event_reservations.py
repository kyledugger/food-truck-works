import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from event_reservations import prepare_event_times, reserve_event
from models import BookingResource, Event, OrganizationStore


class EventReservationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        OrganizationStore.__table__.create(self.engine)
        BookingResource.__table__.create(self.engine)
        Event.__table__.create(self.engine)
        with Session(self.engine) as session:
            store = OrganizationStore(organization_id=1, store_id="truck", poynt_name="Truck",
                                      store_type="food_truck", is_active=True)
            store.booking_resource = BookingResource(name="Truck", capacity=1,
                                                      is_enabled=True, name_follows_store=True)
            session.add(store)
            session.commit()
            self.resource_id = store.booking_resource.id

    def tearDown(self):
        self.engine.dispose()

    def test_local_timezone_and_buffer_instant(self):
        times = prepare_event_times("2026-09-28T10:00", "2026-09-28T11:00",
                                    "America/Phoenix", 30, 45)
        self.assertEqual(times.start_at, datetime(2026, 9, 28, 17, tzinfo=timezone.utc))
        self.assertEqual(times.reserved_start_at, datetime(2026, 9, 28, 16, 30, tzinfo=timezone.utc))
        self.assertEqual(times.reserved_end_at, datetime(2026, 9, 28, 18, 45, tzinfo=timezone.utc))

    def test_overnight_event_uses_venue_zone(self):
        times = prepare_event_times("2026-09-28T23:00", "2026-09-29T01:00",
                                    "America/Denver", 15, 15)
        self.assertEqual(times.start_at, datetime(2026, 9, 29, 5, tzinfo=timezone.utc))
        self.assertEqual((times.end_at - times.start_at).total_seconds(), 7200)

    def test_reject_dst_gap_fold_and_invalid_duration(self):
        for start in ("2026-03-08T02:30", "2026-11-01T01:30"):
            with self.assertRaises(ValueError):
                prepare_event_times(start, "2026-11-02T03:00", "America/New_York", 0, 0)
        with self.assertRaisesRegex(ValueError, "end after"):
            prepare_event_times("2026-09-28T11:00", "2026-09-28T10:00",
                                "America/Phoenix", 0, 0)

    def test_capacity_buffers_adjacent_and_cancellation(self):
        first = prepare_event_times("2026-09-28T10:00", "2026-09-28T11:00",
                                    "America/Phoenix", 30, 30)
        overlap = prepare_event_times("2026-09-28T11:15", "2026-09-28T11:30",
                                      "America/Phoenix", 0, 0)
        adjacent = prepare_event_times("2026-09-28T11:30", "2026-09-28T12:30",
                                       "America/Phoenix", 0, 0)
        with Session(self.engine) as session:
            reserve_event(session, 1, self.resource_id, "First", first)
            session.commit()
        with Session(self.engine) as session:
            with self.assertRaisesRegex(ValueError, "capacity"):
                reserve_event(session, 1, self.resource_id, "Conflict", overlap)
            self.assertEqual(session.scalar(select(func.count(Event.id))), 1)
            reserve_event(session, 1, self.resource_id, "Adjacent", adjacent)
            session.commit()
            self.assertEqual(session.scalar(select(func.count(Event.id))), 2)
            session.execute(update(Event).where(Event.title == "First").values(status="cancelled"))
            session.commit()
            reserve_event(session, 1, self.resource_id, "After cancellation", overlap)
            session.commit()

    def test_capacity_two_and_organization_scope(self):
        times = prepare_event_times("2026-09-28T10:00", "2026-09-28T11:00",
                                    "America/Phoenix", 0, 0)
        with Session(self.engine) as session:
            resource = session.get(BookingResource, self.resource_id)
            resource.capacity = 2
            session.commit()
            for title in ("One", "Two"):
                reserve_event(session, 1, self.resource_id, title, times)
                session.commit()
            with self.assertRaisesRegex(ValueError, "capacity"):
                reserve_event(session, 1, self.resource_id, "Three", times)
            with self.assertRaisesRegex(ValueError, "unavailable"):
                reserve_event(session, 2, self.resource_id, "Other organization", times)


if __name__ == "__main__":
    unittest.main()
