"""Bounded store recovery tests; isolated SQLite and mocked Poynt."""
from datetime import timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy import select
import test_kitchen
from test_live_dashboard import sale
from live_dashboard_models import DashboardOrder, DashboardSync
from kitchen_models import KitchenSession
from live_dashboard_service import save_order
from live_dashboard_metrics import instant
from store_time import utc_now


class RecoveryTests(test_kitchen.KitchenTests):
    def test_recent_scoped_provider_window_filters_old_and_other_store(self):
        now=utc_now()
        rows=[sale(now-timedelta(minutes=2),'good'),sale(now-timedelta(minutes=11),'old'),
              sale(now-timedelta(minutes=1),'other',store='popup'),sale(now+timedelta(seconds=1),'future')]
        mock=AsyncMock(return_value=rows)
        with self.factory() as session:
            session.get(KitchenSession,1).started_at=now-timedelta(minutes=1)
            before=session.get(DashboardSync,1).last_reconciled_at
            session.commit()
        with patch('routers.kitchen.utc_now',return_value=now),patch('poynt.client.PoyntClient.get_recent_orders',new=mock):
            response=self.client.post('/dashboard/stores/1/kitchen/refresh?recent_minutes=10',headers={'X-Kitchen-CSRF':self.csrf})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(mock.await_count,1)
        self.assertEqual(mock.call_args.kwargs['start_at'],(now-timedelta(minutes=10)).isoformat().replace('+00:00','Z'))
        self.assertEqual(mock.call_args.kwargs['end_at'],now.isoformat().replace('+00:00','Z'))
        with self.factory() as session:
            self.assertEqual([o.order_id for o in session.scalars(select(DashboardOrder)).all()],['good'])
            self.assertEqual(session.get(DashboardSync,1).last_reconciled_at,before)
        result=self.service('recent',recovery_at=response.json()['recovery_at']).json()
        self.assertEqual([o['id'] for o in result['orders']],['good'])
        self.assertEqual(self.service('import',recovery_at=result['recovery_at'],order_ids=['good']).status_code,200)

    def test_recent_window_uses_search_time_not_old_service_start(self):
        now=utc_now()
        with self.factory() as session:
            tracking=session.get(KitchenSession,1);tracking.started_at=now-timedelta(hours=3)
            for payload in [sale(now-timedelta(minutes=3),'new'),sale(now-timedelta(hours=3,minutes=3),'old'),sale(now-timedelta(minutes=3),'other',store='popup')]:
                session.add(DashboardOrder(organization_id=1,business_id='business',order_id=payload['id'],store_id=payload['context']['storeId'],created_at=instant(payload['createdAt']),provider_updated_at=instant(payload['updatedAt']),payload=payload))
            session.commit()
        # Calling service directly avoids queue admission of cached orders during setup.
        from types import SimpleNamespace
        from routers.kitchen import service,ServiceAction
        request=SimpleNamespace(session={'user_id':1,'organization_id':1,'kitchen_csrf':self.csrf},headers={'x-kitchen-csrf':self.csrf},url=SimpleNamespace(path='/dashboard/stores/1/kitchen/service'))
        with self.factory() as session:revision=session.get(KitchenSession,1).revision
        result=service(request,1,ServiceAction(action='recent',revision=revision))
        self.assertEqual([o['id'] for o in result['orders']],['new'])
        self.assertEqual(self.service('import',recovery_at=(now-timedelta(hours=1)).isoformat(),order_ids=[]).status_code,409)
