"""Focused report tests without importing the app or opening its database."""
import ast
from pathlib import Path
import unittest
from urllib.parse import urlencode, urlsplit, parse_qs


def load_helpers():
    source = Path(__file__).resolve().parents[1] / "routers" / "poynt.py"
    names = {"_tip_range_totals", "_tip_report_redirect"}
    nodes = [node for node in ast.parse(source.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    class Response:
        def __init__(self, url, status_code):
            self.url, self.status_code = url, status_code
    scope = {"RedirectResponse": Response, "urlencode": urlencode}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    return scope["_tip_range_totals"], scope["_tip_report_redirect"]


totals, redirect = load_helpers()


def payout(employee_id, name, cents, method, status):
    return dict(employee_id=employee_id, employee_name=name, amount_cents=cents,
                method=method, status=status)


class TipReportTotalsTests(unittest.TestCase):
    def setUp(self):
        self.submissions = [dict(payouts=[
            payout(1, "Alex", 101, "cash", "paid"),
            payout(1, "Alex", 202, "cash", "pending"),
            payout(1, "Alex", 303, "paycheck", "pending"),
            payout(1, "Alex", 404, "paycheck", "paid"),
            payout(1, "Alex", 999, "paycheck", "rejected"),
            payout(2, "Alex", 505, "paycheck", "pending"),
        ])]

    def test_cents_methods_statuses_and_same_name_employees(self):
        result = totals(self.submissions)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], dict(name="Alex", cash_cents=303,
            cash_paid_cents=101, cash_pending_cents=202, paycheck_cents=707,
            paycheck_paid_cents=404, paycheck_pending_cents=303))
        self.assertEqual(result[1]["paycheck_cents"], 505)

    def test_employee_scope_and_unlinked_account(self):
        self.assertEqual(len(totals(self.submissions, {2})), 1)
        self.assertEqual(totals(self.submissions, {2})[0]["paycheck_cents"], 505)
        self.assertEqual(totals(self.submissions, set()), [])

    def test_legacy_allocations_and_rejected_submission(self):
        legacy = dict(payouts=[], employee_totals=[dict(id=1, name="Alex", total_tip_cents=123)],
                      payout_method="cash", processing_status="paid")
        self.assertEqual(totals([legacy])[0]["cash_paid_cents"], 123)
        legacy["processing_status"] = "rejected"
        self.assertEqual(totals([legacy]), [])

    def test_multiple_submissions_and_employee_name_change(self):
        self.submissions.append(dict(payouts=[payout(1, "Alex Smith", 7, "cash", "paid")]))
        self.assertEqual(totals(self.submissions)[0]["cash_cents"], 310)

    def test_redirect_preserves_filters_and_encodes_store(self):
        result = redirect("store & 1", "2026-09-21", "2026-09-28", "paycheck", "pending")
        self.assertEqual(result.status_code, 303)
        self.assertEqual(urlsplit(result.url).path, "/poynt/tip-submissions")
        self.assertEqual(parse_qs(urlsplit(result.url).query), {
            "store_id": ["store & 1"], "start": ["2026-09-21"], "end": ["2026-09-28"],
            "payment": ["paycheck"], "status": ["pending"]})

    def test_invalid_filter_values_are_omitted(self):
        result = redirect("store", payment="invalid", status="invalid")
        self.assertEqual(parse_qs(urlsplit(result.url).query), {"store_id": ["store"]})


if __name__ == "__main__":
    unittest.main()
