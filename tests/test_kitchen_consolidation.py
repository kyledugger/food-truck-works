"""Consolidated views use isolated SQLite; never contact Poynt."""
from datetime import timedelta
from sqlalchemy import select, func
from test_kitchen import KitchenTests
from kitchen_models import KitchenSession, KitchenTicket


class ConsolidationTests(KitchenTests):
    def test_watch_feed_cannot_mutate_and_does_not_heartbeat_or_admit(self):
        self.seed()
        with self.factory() as session:
            before = session.get(KitchenSession, 1).last_seen_at
            count = session.scalar(select(func.count(KitchenTicket.id)))
        response = self.client.get('/dashboard/stores/1/kitchen-watch')
        self.assertEqual(response.status_code, 200)
        with self.factory() as session:
            self.assertEqual(session.get(KitchenSession, 1).last_seen_at, before)
            self.assertEqual(session.scalar(select(func.count(KitchenTicket.id))), count)
        for suffix in ('', '/service', '/refresh', '/1'):
            response = self.client.post('/dashboard/stores/1/kitchen-watch'+suffix,
                headers={'X-Kitchen-CSRF': self.csrf}, json={'action':'done','revision':0})
            self.assertIn(response.status_code, (404, 405))

    def test_off_service_has_no_metrics(self):
        self.service('end')
        flow = self.queue()['flow']
        self.assertFalse(flow['tracking'])
        self.assertIsNone(flow['intake'])
        self.assertIsNone(flow['completion'])
        self.assertIsNone(flow['customer_wait'])
        self.assertEqual(flow['order_pace'], [])

    def test_dashboard_wait_is_live_only_and_home_has_no_performance(self):
        self.seed()
        self.queue()
        live = self.client.get('/dashboard/data?date=today').json()['stores'][0]
        self.assertTrue(live['kitchen_tracking'])
        self.assertIsNotNone(live['customer_wait'])
        historical = self.client.get('/dashboard/data?date=yesterday').json()['stores'][0]
        self.assertNotIn('customer_wait', historical)
        home = self.client.get('/dashboard/stores/1/home')
        self.assertNotIn('Store Performance', home.text)
        view = self.client.get('/dashboard/stores/1/kitchen-display?readonly=true')
        self.assertIn('data-readonly="true"', view.text)
        self.assertIn('data-csrf=""', view.text)

    def test_retired_performance_redirects(self):
        response = self.client.get('/dashboard/stores/1', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/dashboard/stores/1/kitchen-display')
