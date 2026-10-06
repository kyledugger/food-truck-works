"""Isolated refresh tests. No live database or Poynt requests."""
import asyncio
import os
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL', 'sqlite://')
from poynt.client import PoyntClient, PoyntAPIError
from poynt.connection import PoyntCredentials


class RefreshConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.row = SimpleNamespace(
            business_id='business', access_token='old-access',
            refresh_token='old-refresh', token_type='BEARER',
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1))
        self.lock = Lock()
        self.calls = []

    def client(self):
        return PoyntClient(PoyntCredentials(**vars(self.row)), 1)

    @contextmanager
    def locked(self, organization_id):
        with self.lock:
            yield self.row

    async def provider(self, token):
        self.calls.append(token)
        await asyncio.sleep(.02)
        return dict(accessToken='new-access', refreshToken='new-refresh',
                    expiresIn=86400, tokenType='BEARER')

    async def test_overlapping_clients_refresh_once_and_adopt_rotation(self):
        clients = [self.client() for _ in range(8)]
        with patch('poynt.client.locked_poynt_connection', self.locked), patch(
                'poynt.client.refresh_access_token', self.provider):
            await asyncio.gather(*(client.refresh() for client in clients))
        self.assertEqual(self.calls, ['old-refresh'])
        self.assertTrue(all(c.access_token == 'new-access' for c in clients))
        self.assertTrue(all(c.refresh_token == 'new-refresh' for c in clients))

    async def test_valid_but_stale_client_adopts_reconnected_credentials(self):
        self.row.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        client = self.client()
        self.row.access_token = 'reconnected'
        with patch('poynt.client.locked_poynt_connection', self.locked), patch(
                'poynt.client.refresh_access_token', self.provider):
            await client.refresh()
        self.assertEqual(client.access_token, 'reconnected')
        self.assertEqual(self.calls, [])

    async def test_failure_leaves_credentials_and_releases_lock(self):
        client = self.client()
        async def failed(token):
            raise PoyntAPIError('provider unavailable')
        with patch('poynt.client.locked_poynt_connection', self.locked), patch(
                'poynt.client.refresh_access_token', failed):
            with self.assertRaises(PoyntAPIError):
                await client.refresh()
        self.assertEqual(self.row.access_token, 'old-access')
        self.assertEqual(client.access_token, 'old-access')
        with patch('poynt.client.locked_poynt_connection', self.locked), patch(
                'poynt.client.refresh_access_token', self.provider):
            await client.refresh()
        self.assertEqual(client.access_token, 'new-access')

    async def test_commit_failure_does_not_publish_client_credentials(self):
        @contextmanager
        def failed_commit(org):
            yield self.row
            raise RuntimeError('commit failed')
        client = self.client()
        with patch('poynt.client.locked_poynt_connection', failed_commit), patch(
                'poynt.client.refresh_access_token', self.provider):
            with self.assertRaises(RuntimeError):
                await client.refresh()
        self.assertEqual(client.access_token, 'old-access')


if __name__ == '__main__':
    unittest.main()
