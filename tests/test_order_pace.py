import unittest
from datetime import datetime, timedelta, timezone
from live_dashboard_metrics import order_pace, kitchen_intake

class OrderPaceTests(unittest.TestCase):
    def test_kitchen_intake_windows_and_quantity(self):
        now = datetime(2026, 10, 5, 7, 2, tzinfo=timezone.utc)
        def order(minutes, quantity, state="COMPLETED"):
            return {"createdAt": (now-timedelta(minutes=minutes)).isoformat(), "items": [{"quantity": quantity}], "statuses": {"transactionStatusSummary": state}}
        result = kitchen_intake([order(5, 5), order(0, 7), order(20, 15), order(21, 99), order(-1, 99), order(1, 99, "OPEN"), order(1, 99, "CANCELLED")], now)
        self.assertEqual(result["recent_items"], 12)
        self.assertEqual(result["recent_orders"], 2)
        self.assertEqual(result["items_per_minute"], 2.4)
        self.assertEqual(result["baseline_items_per_minute"], 1)
        self.assertEqual(result["trend"], "rising")
        self.assertEqual(kitchen_intake([], now)["trend"], "steady")
        self.assertEqual(kitchen_intake([order(10, 15)], now)["trend"], "falling")
        self.assertEqual(kitchen_intake([order(1, 5), order(10, 15)], now)["trend"], "steady")

    def test_groups_recent_and_day_benchmark(self):
        now = datetime(2026, 10, 5, 20, tzinfo=timezone.utc)
        def order(seconds, units=1, state="COMPLETED"):
            return {"createdAt": (now-timedelta(seconds=seconds)).isoformat(), "items": [{"quantity": units}], "statuses": {"transactionStatusSummary": state}}
        data = [order(1200), order(1100, 1), order(180, 2), order(120, 1), order(90, 3), order(30, 2), order(10, 1, "CANCELLED")]
        result = {m["items"]: m for m in order_pace(data, now-timedelta(hours=8), now+timedelta(hours=4), now)}
        self.assertEqual(result[1]["recent_seconds"], 60)
        self.assertEqual(result[1]["fast_seconds"], 60)
        self.assertEqual(result[2]["recent_seconds"], 490)
        self.assertEqual(result[2]["recent_samples"], 2)
        self.assertEqual(result[3]["recent_seconds"], 30)

    def test_fastest_ten_percent_cap_and_recent_expiry(self):
        now = datetime(2026, 10, 5, 20, tzinfo=timezone.utc)
        start = now-timedelta(hours=8)
        orders = [{"createdAt": (start+timedelta(seconds=i*10)).isoformat(), "items": [{"quantity": 1}], "statuses": {"transactionStatusSummary": "COMPLETED"}} for i in range(151)]
        result = order_pace(orders, start, now+timedelta(hours=4), now)[0]
        self.assertEqual(result["fast_seconds"], 10)
        self.assertEqual(result["today_samples"], 150)
        self.assertIsNone(result["recent_seconds"])
        self.assertEqual(result["recent_samples"], 0)

    def test_empty_invalid_and_boundary(self):
        now = datetime(2026, 10, 5, 20, tzinfo=timezone.utc)
        orders = [{"createdAt": "2026-10-05T20:00:00", "statuses": {"transactionStatusSummary": "COMPLETED"}}]
        result = order_pace(orders, now, now+timedelta(days=1), now)
        self.assertTrue(all(m["recent_seconds"] is None and m["fast_seconds"] is None for m in result))

    def test_elapsed_across_repeated_clock_hour(self):
        orders = [{"createdAt": at, "items": [{"quantity": 1}], "statuses": {"transactionStatusSummary": "COMPLETED"}} for at in ["2026-11-01T01:59:30-04:00", "2026-11-01T01:00:30-05:00"]]
        now = datetime(2026, 11, 1, 6, 1, tzinfo=timezone.utc)
        result = order_pace(orders, now-timedelta(hours=3), now+timedelta(hours=3), now)
        self.assertEqual(result[0]["recent_seconds"], 60)

if __name__ == "__main__": unittest.main()
