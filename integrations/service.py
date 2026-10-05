from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from integrations.crypto import encrypt, decrypt
from integrations.models import IntegrationConnection, IntegrationMapping
from integrations.square import SquareProvider, SquareConfig, SquareError
from models import OrganizationStore, Employee
from store_time import utc_now


def save_tokens(connection, result):
    expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None:
        raise ValueError("Square returned a naive token expiration")
    connection.access_token_encrypted = encrypt(result["access_token"])
    if result.get("refresh_token"):
        connection.refresh_token_encrypted = encrypt(result["refresh_token"])
    connection.expires_at = expiry.astimezone(timezone.utc)
    connection.refreshed_at = utc_now()
    connection.status = "connected"


async def directory(session, connection):
    # The row lock serializes refreshes across workers (PostgreSQL).
    connection = session.scalar(select(IntegrationConnection).where(
        IntegrationConnection.id == connection.id).with_for_update())
    if not connection.access_token_encrypted or connection.status == "disconnected":
        raise ValueError("Connect Square first")
    provider = SquareProvider(SquareConfig.load(connection.environment))
    try:
        if not connection.expires_at or connection.expires_at <= utc_now() + timedelta(minutes=5) or not connection.refreshed_at or connection.refreshed_at < utc_now() - timedelta(days=7):
            if not connection.refresh_token_encrypted:
                raise SquareError(401)
            result = await provider.refresh(decrypt(connection.refresh_token_encrypted))
            if result.get("merchant_id", connection.external_account_id) != connection.external_account_id:
                raise ValueError("Square account changed during refresh")
            save_tokens(connection, result)
            # Persist refreshed credentials even if subsequent directory calls fail.
            session.commit()
        token = decrypt(connection.access_token_encrypted)
        connection.granted_scopes = await provider.scopes(token)
        locations = await provider.locations(token)
        employees = await provider.employees(token)
        connection.status = "connected"
        connection.verified_at = utc_now()
        session.commit()
        return locations, employees
    except SquareError as exc:
        connection.status = "reconnect_required" if exc.status == 401 else "error"
        session.commit()
        raise


def set_mapping(session, connection, kind, external_id, target_id):
    if kind not in {"location", "employee"}:
        raise ValueError("Invalid mapping type")
    model = OrganizationStore if kind == "location" else Employee
    target = session.get(model, target_id)
    if not target or target.organization_id != connection.organization_id:
        raise ValueError("Choose a record belonging to this business")
    column = IntegrationMapping.store_id if kind == "location" else IntegrationMapping.employee_id
    existing_target = session.scalar(select(IntegrationMapping).where(
        IntegrationMapping.connection_id == connection.id, column == target_id))
    if existing_target and existing_target.external_id != external_id:
        raise ValueError("This record is already mapped to another Square record")
    mapping = session.scalar(select(IntegrationMapping).where(
        IntegrationMapping.connection_id == connection.id, IntegrationMapping.kind == kind,
        IntegrationMapping.external_id == external_id))
    if not mapping:
        mapping = IntegrationMapping(connection_id=connection.id, kind=kind, external_id=external_id)
        session.add(mapping)
    mapping.store_id = target_id if kind == "location" else None
    mapping.employee_id = target_id if kind == "employee" else None
    return mapping
