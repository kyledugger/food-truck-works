import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from claim_tip_windows import minute_floor, minute_ceiling, window_bounds, parse_start, validate_window


class ClaimWindowTests(unittest.TestCase):
    def setUp(self):
        self.launch = datetime(2026, 10, 5, 8, 30, 47, tzinfo=timezone.utc)  # Phoenix 1:30 AM

    def test_launch_floor_and_legacy_ceiling(self):
        self.assertEqual(minute_floor(self.launch).second, 0)
        self.assertEqual(minute_ceiling(self.launch).minute, 31)
        self.assertEqual(minute_ceiling(minute_floor(self.launch)), minute_floor(self.launch))

    def test_normal_midnight_default(self):
        lower, end = window_bounds(self.launch, 'America/Phoenix', date(2026,10,5), False)
        self.assertEqual(lower.hour, 7)
        self.assertEqual(end, minute_floor(self.launch))

    def test_overnight_last_claim_and_gap(self):
        last = datetime(2026,10,5,4,tzinfo=timezone.utc)  # yesterday 9 PM
        lower, end = window_bounds(self.launch,'America/Phoenix',date(2026,10,4),False,last)
        self.assertEqual(lower,last)
        validate_window(last + timedelta(hours=1),end,lower,end)
        with self.assertRaises(ValueError): validate_window(last-timedelta(minutes=1),end,lower,end)

    def test_employee_dates_and_24_hour_limit(self):
        with self.assertRaises(ValueError): window_bounds(self.launch,'America/Phoenix',date(2026,10,3),False)
        with self.assertRaises(ValueError): window_bounds(self.launch,'America/Phoenix',date(2026,10,6),True)
        lower,end=window_bounds(self.launch,'America/Phoenix',date(2026,10,4),False)
        self.assertEqual(end-lower,timedelta(hours=24))

    def test_historical_manager_and_activation(self):
        lower,end=window_bounds(self.launch,'America/Phoenix',date(2026,10,1),True)
        self.assertEqual(end.astimezone(ZoneInfo('America/Phoenix')).date(),date(2026,10,2))
        activation=lower+timedelta(hours=12,seconds=3)
        lower2,_=window_bounds(self.launch,'America/Phoenix',date(2026,10,1),True,activation=activation)
        self.assertEqual(lower2,minute_ceiling(activation))

    def test_dst_days_and_bad_wall_times(self):
        launch=datetime(2026,11,3,12,tzinfo=timezone.utc)
        lower,end=window_bounds(launch,'America/New_York',date(2026,11,1),True)
        self.assertEqual(end-lower,timedelta(hours=24))  # 25-hour day trimmed to 24 elapsed hours
        launch=datetime(2026,3,10,12,tzinfo=timezone.utc)
        lower,end=window_bounds(launch,'America/New_York',date(2026,3,8),True)
        self.assertEqual(end-lower,timedelta(hours=23))
        for day,clock in [(date(2026,3,8),'02:30'),(date(2026,11,1),'01:30')]:
            with self.assertRaises(ValueError): parse_start(day,clock,'America/New_York')

    def test_seconds_and_empty_windows_rejected(self):
        lower,end=window_bounds(self.launch,'America/Phoenix',date(2026,10,5),False)
        with self.assertRaises(ValueError): validate_window(lower,end+timedelta(seconds=1),lower,end)
        with self.assertRaises(ValueError): validate_window(end,end,lower,end)
        with self.assertRaises(ValueError): parse_start(date(2026,10,5),'01:00:00','America/Phoenix')

    def test_named_zone_does_not_use_browser_timezone(self):
        phoenix=parse_start(date(2026,10,5),'09:00','America/Phoenix')
        ny=parse_start(date(2026,10,5),'09:00','America/New_York')
        self.assertEqual(phoenix-ny,timedelta(hours=3))


if __name__ == '__main__': unittest.main()
