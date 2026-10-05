import os
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
import unittest
from unittest.mock import patch
from datetime import timedelta
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
import httpx
from models import Organization, Employee, OrganizationStore
from integrations.models import IntegrationConnection, IntegrationMapping, IntegrationOAuthAttempt
from integrations.crypto import encrypt, decrypt
from integrations.providers import SQUARE
from integrations.square import SquareProvider, SquareConfig, SquareError
from integrations.service import set_mapping, save_tokens
from routers import integrations as routes
from store_time import utc_now


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        for table in (Organization.__table__, Employee.__table__, OrganizationStore.__table__, IntegrationConnection.__table__, IntegrationMapping.__table__):
            table.create(self.engine)
        self.key = Fernet.generate_key().decode()
        self.env = patch.dict(os.environ, {"INTEGRATION_TOKEN_KEYS": self.key})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.engine.dispose()

    def test_encryption_rotation_and_capabilities(self):
        value = encrypt("private-access-token")
        self.assertNotIn("private-access-token", value)
        self.assertEqual(decrypt(value), "private-access-token")
        with patch.dict(os.environ, {"INTEGRATION_TOKEN_KEYS": Fernet.generate_key().decode() + "," + self.key}):
            self.assertEqual(decrypt(value), "private-access-token")
        self.assertEqual(SQUARE.authorized(["MERCHANT_PROFILE_READ", "EMPLOYEES_READ"]), {"locations.read", "employees.read"})
        self.assertNotIn("payroll.write", SQUARE.capabilities)

    def test_mapping_tenant_boundary_and_duplicates(self):
        with Session(self.engine, expire_on_commit=False) as session:
            c = IntegrationConnection(organization_id=1, provider="square", environment="sandbox", external_account_id="merchant")
            a = Employee(organization_id=1, first_name="A", last_name="B", email="a@example.test")
            b = Employee(organization_id=2, first_name="C", last_name="D", email="b@example.test")
            session.add_all([c, a, b]); session.commit()
            with self.assertRaises(ValueError):
                set_mapping(session, c, "employee", "ext-b", b.id)
            set_mapping(session, c, "employee", "ext-a", a.id); session.commit()
            with self.assertRaises(ValueError):
                set_mapping(session, c, "employee", "another", a.id)
            self.assertEqual(session.scalar(select(IntegrationMapping)).employee_id, a.id)

    def test_account_cannot_cross_organizations(self):
        with Session(self.engine, expire_on_commit=False) as session:
            for org in (1, 2):
                session.add(IntegrationConnection(organization_id=org, provider="square", environment="sandbox", external_account_id="same"))
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_token_expiry_is_aware(self):
        c = IntegrationConnection()
        save_tokens(c, {"expires_at": "2026-10-30T00:00:00Z", "access_token": "secret", "refresh_token": "refresh"})
        self.assertIsNotNone(c.expires_at.tzinfo)
        self.assertEqual(decrypt(c.refresh_token_encrypted), "refresh")
        with self.assertRaises(ValueError):
            save_tokens(c, {"expires_at": "2026-10-30T00:00:00", "access_token": "secret"})

    def test_permissions_csrf_and_ownership(self):
        class Request:
            session = {"user_id": 1, "integration_csrf": "good"}
        request = Request()
        with patch.object(routes, "get_current_organization_id", return_value=1), patch.object(routes, "get_organization_role", return_value="member"):
            with self.assertRaises(Exception) as error:
                routes.authorize(request)
            self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(Exception) as error:
            routes.csrf(request, "bad")
        self.assertEqual(error.exception.status_code, 403)
        with Session(self.engine, expire_on_commit=False) as session:
            c = IntegrationConnection(organization_id=2, provider="square", environment="sandbox", external_account_id="other")
            session.add(c); session.commit()
            with self.assertRaises(Exception) as error:
                routes.owned(session, 1, c.id)
            self.assertEqual(error.exception.status_code, 404)

    def test_unauthenticated_routes(self):
        app = FastAPI(); app.add_middleware(SessionMiddleware, secret_key="test-only"); app.include_router(routes.router)
        with TestClient(app) as client:
            self.assertEqual(client.get("/integrations").status_code, 401)
            self.assertEqual(client.get("/integrations/square/callback?state=invalid").status_code, 401)

    def test_callback_rejects_wrong_state_before_database_access(self):
        app = FastAPI(); app.add_middleware(SessionMiddleware, secret_key="test-only"); app.include_router(routes.router)
        with patch.object(routes, "authorize", return_value=(1, 1)), TestClient(app) as client:
            self.assertEqual(client.get("/integrations/square/callback?state=invalid&code=code").status_code, 400)

    def test_migration_upgrade_downgrade_isolated_database(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        path = Path(__file__).parents[1] / "alembic/versions/a61f0d8c3b92_integration_foundation.py"
        spec = importlib.util.spec_from_file_location("foundation_migration", path)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(set(inspect(connection).get_table_names()), {"integration_connections", "integration_mappings", "integration_oauth_attempts"})
                migration.downgrade()
                self.assertEqual(inspect(connection).get_table_names(), [])
        engine.dispose()


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_directory_pagination_and_request_headers(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer test-token")
            if len(calls) == 1:
                return httpx.Response(200, json={"team_members": [{"id": "1", "given_name": "A"}], "cursor": "next"})
            self.assertIn(b'"cursor":"next"', request.content)
            return httpx.Response(200, json={"team_members": [{"id": "2", "given_name": "B"}]})
        provider = SquareProvider(SquareConfig("sandbox", "app", "secret", "https://example.test/callback", "2026-09-16"), httpx.MockTransport(handler))
        self.assertEqual([x["id"] for x in await provider.employees("test-token")], ["1", "2"])
        self.assertTrue(str(calls[0].url).startswith("https://connect.squareupsandbox.com/"))
        self.assertNotIn("secret", provider.authorization_url("state"))

    async def test_provider_error_does_not_leak_secrets(self):
        provider = SquareProvider(SquareConfig("production", "app", "secret", "https://example.test/callback", "2026-09-16"),
            httpx.MockTransport(lambda request: httpx.Response(401, json={"errors": [{"detail": "sensitive"}]})))
        with self.assertRaises(SquareError) as error:
            await provider.locations("token")
        self.assertEqual(error.exception.status, 401)
        self.assertNotIn("sensitive", str(error.exception))


if __name__ == "__main__":
    unittest.main()
