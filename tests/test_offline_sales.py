"""Shared sales rules; no application startup, database, or API access."""
import ast
from datetime import datetime
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest

from live_dashboard_metrics import completed, order_store, money, summarize


def report_functions():
    path = Path(__file__).resolve().parents[1] / 'routers' / 'poynt.py'
    names = {'get_available_store_ids', 'filter_orders_by_stores',
             'filter_completed_orders', 'calculate_order_metrics'}
    nodes = [n for n in ast.parse(path.read_text()).body
             if isinstance(n, ast.FunctionDef) and n.name in names]
    scope = dict(completed_sale=completed, order_store=order_store, order_money=money,
                 datetime=datetime, logger=logging.getLogger(__name__))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


def order(number, cents=750, payment='NONE', status='COMPLETED', transactions=None):
    return {'id': str(number), 'createdAt': '2026-11-14T21:23:00Z',
            'context': {'storeId': 'store'},
            'statuses': {'status': status, 'transactionStatusSummary': payment},
            'transactions': transactions or [], 'amounts': {'netTotal': cents},
            'items': [{'name': 'Coffee', 'quantity': 1, 'unitPrice': cents}]}


class OfflineSalesTests(unittest.TestCase):
    def test_reconcile_18_orders_and_15824_cents(self):
        # Amount distribution from the diagnosed incident, without real IDs.
        offline = [order(i, c) for i, c in enumerate([750] * 10 + [1499, 1500, 1500])]
        paid = [order(20+i, c, 'COMPLETED', transactions=[{'context': {'storeId': 'store'}}])
                for i, c in enumerate([1500, 750, 75, 750, 750])]
        orders = offline + paid
        f = report_functions()
        self.assertEqual(f['get_available_store_ids'](offline), ['store'])
        selected = f['filter_orders_by_stores'](orders, ['store'])
        accepted, rejected = f['filter_completed_orders'](selected)
        self.assertEqual((len(accepted), rejected), (18, 0))
        report = f['calculate_order_metrics'](accepted)
        store = SimpleNamespace(timezone_name='America/Phoenix', id=1,
                                display_name='Store', poynt_name='Store', store_id='store')
        dashboard = summarize(store, orders, datetime.fromisoformat('2026-11-15T06:00:00+00:00'))
        self.assertEqual(round(report['total_revenue'] * 100), 15824)
        self.assertEqual(sum(money(o)[0] for o in orders if completed(o)), 15824)
        self.assertEqual(dashboard['sales_cents'], 15824)
        self.assertEqual(report['total_tips'], 0)

    def test_unfinished_cancelled_and_failed_remain_excluded(self):
        for status, payment in [('OPEN','NONE'), ('CANCELLED','COMPLETED'),
                                ('COMPLETED','DECLINED'), ('COMPLETED','PENDING')]:
            with self.subTest(status=status, payment=payment):
                self.assertFalse(completed(order(1, status=status, payment=payment)))

    def test_unconfirmed_attached_payment_does_not_use_offline_fallback(self):
        self.assertFalse(completed(order(1, transactions=[{'status': 'DECLINED'}])))
        cached = order(1)
        cached['_has_transactions'] = True
        self.assertFalse(completed(cached))
        cached['_has_transactions'] = False
        self.assertTrue(completed(cached))

    def test_store_precedence_and_ambiguous_fallback(self):
        self.assertEqual(order_store({'context': {'storeId': 'A'},
            'transactions': [{'context': {'storeId': 'B'}}]}), 'A')
        self.assertEqual(order_store({'transactions': [{'context': {'storeId': 'B'}}]}), 'B')
        self.assertIsNone(order_store({'transactions': [{'context': {'storeId': s}} for s in ('A','B')]}))

    def test_refund_and_null_total_agree(self):
        value = order(1, payment='PARTIALLY_REFUNDED')
        value['amounts'] = {'netTotal': None, 'orderAmount': 1500,
            'capturedTotals': {'orderAmount': 1500, 'tipAmount': 200},
            'refundedTotals': {'orderAmount': 750, 'tipAmount': 100}}
        self.assertTrue(completed(value))
        self.assertEqual(money(value), (750, 100))
        metrics = report_functions()['calculate_order_metrics']([value])
        self.assertEqual((metrics['total_revenue'], metrics['total_tips']), (7.5, 1))


if __name__ == '__main__':
    unittest.main()
