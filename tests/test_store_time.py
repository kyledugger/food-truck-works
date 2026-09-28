import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from store_time import local_day_bounds, local_to_utc, utc_iso


class StoreTimeTests(unittest.TestCase):
    def test_phoenix_wall_time(self):
        self.assertEqual(
            local_to_utc(datetime(2026, 9, 27, 9), ZoneInfo("America/Phoenix")).isoformat(),
            "2026-09-27T16:00:00+00:00",
        )

    def test_daylight_saving_days(self):
        zone = ZoneInfo("America/New_York")
        for day, expected_hours in ((date(2026, 3, 8), 23), (date(2026, 11, 1), 25)):
            start, end = local_day_bounds(day, zone)
            self.assertEqual((end - start).total_seconds(), expected_hours * 3600)

    def test_missing_and_repeated_wall_times(self):
        zone = ZoneInfo("America/New_York")
        for local in (datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)):
            with self.assertRaises(ValueError):
                local_to_utc(local, zone)

    def test_utc_naive_database_serialization(self):
        self.assertEqual(utc_iso(datetime(2026, 9, 27, 16)), "2026-09-27T16:00:00Z")


if __name__ == "__main__":
    unittest.main()
