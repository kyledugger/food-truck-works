import hashlib
import asyncio
import secrets
from datetime import timedelta
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from cryptography.fernet import InvalidToken
from database import SessionLocal
from models import OrganizationStore, Employee
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_integrations
from integrations.models import IntegrationConnection, IntegrationMapping, IntegrationOAuthAttempt
from integrations.providers import SQUARE
from integrations.square import SquareProvider, SquareConfig, SquareError
from integrations.crypto import cipher, decrypt
from integrations.service import save_tokens, directory, set_mapping
from store_time import utc_now

router = APIRouter(prefix="/integrations", tags=["integrations"])
templates = Jinja2Templates(directory="templates")


def authorize(request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(401, "Sign in first")
    org_id = get_current_organization_id(request)
    if not org_id or not role_can_manage_integrations(get_organization_role(user_id, org_id)):
        raise HTTPException(403, "Only business owners and managers can manage integrations")
    return user_id, org_id


def csrf(request, value):
    expected = request.session.get("integration_csrf")
    if not expected or not secrets.compare_digest(expected, value):
        raise HTTPException(403, "Reload the integrations page and try again")


def owned(session, org_id, connection_id):
    connection = session.get(IntegrationConnection, connection_id)
    if not connection or connection.organization_id != org_id or connection.provider != "square":
        raise HTTPException(404, "Connection not found")
    return connection


def finish(request, message):
    request.session["integration_notice"] = message
    return RedirectResponse("/integrations", status_code=303)


@router.get("")
def index(request: Request):
    _, org_id = authorize(request)
    request.session.setdefault("integration_csrf", secrets.token_urlsafe(32))
    notices = request.session.pop("integration_notice", None)
    cards = []
    with SessionLocal() as session:
        connections = session.scalars(select(IntegrationConnection).where(IntegrationConnection.organization_id == org_id)).all()
        stores = session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id == org_id)).all()
        employees = session.scalars(select(Employee).where(Employee.organization_id == org_id)).all()
        for connection in connections:
            error, locations, team = None, [], []
            if connection.status != "disconnected":
                try:
                    locations, team = asyncio.run(directory(session, connection))
                except (SquareError, ValueError, RuntimeError, InvalidToken):
                    error = "Could not verify this connection. Check configuration or reconnect Square."
            mappings = session.scalars(select(IntegrationMapping).where(IntegrationMapping.connection_id == connection.id)).all()
            # Pass only safe presentation fields to templates, never credentials.
            cards.append({"id": connection.id, "account": connection.external_account_id,
                "environment": connection.environment, "status": connection.status,
                "verified_at": connection.verified_at, "error": error,
                "capabilities": sorted(SQUARE.authorized(connection.granted_scopes)),
                "locations": locations, "employees": team,
                "mappings": {(m.kind, m.external_id): m.store_id or m.employee_id for m in mappings}})
        return templates.TemplateResponse(request=request, name="integrations.html", context={
            "cards": cards, "stores": stores, "employees": employees,
            "csrf_token": request.session["integration_csrf"], "notice": notices})


@router.post("/square/connect")
def connect(request: Request, environment: str = Form(...), csrf_token: str = Form(...)):
    user_id, org_id = authorize(request)
    csrf(request, csrf_token)
    try:
        config = SquareConfig.load(environment)
        cipher()  # Fail before sending the user to Square if storage is unconfigured.
    except (RuntimeError, ValueError):
        return finish(request, "Square configuration is incomplete. See INTEGRATION_FOUNDATION_INSTALL.md.")
    state = secrets.token_urlsafe(32)
    with SessionLocal() as session:
        session.add(IntegrationOAuthAttempt(state_hash=hashlib.sha256(state.encode()).hexdigest(),
            organization_id=org_id, user_id=user_id, provider="square", environment=environment,
            expires_at=utc_now() + timedelta(minutes=10)))
        session.commit()
    request.session["square_oauth_state"] = state
    return RedirectResponse(SquareProvider(config).authorization_url(state), status_code=303)


@router.get("/square/callback")
def callback(request: Request):
    user_id, org_id = authorize(request)
    state = request.query_params.get("state", "")
    expected = request.session.pop("square_oauth_state", "")
    if not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(400, "Invalid Square authorization state")
    with SessionLocal() as session:
        # Atomic consumption prevents callback replay across concurrent requests.
        attempt = session.scalar(update(IntegrationOAuthAttempt).where(
            IntegrationOAuthAttempt.state_hash == hashlib.sha256(state.encode()).hexdigest(),
            IntegrationOAuthAttempt.user_id == user_id, IntegrationOAuthAttempt.organization_id == org_id,
            IntegrationOAuthAttempt.provider == "square", IntegrationOAuthAttempt.used_at.is_(None),
            IntegrationOAuthAttempt.expires_at > utc_now()).values(used_at=utc_now()).returning(IntegrationOAuthAttempt))
        if not attempt:
            raise HTTPException(400, "Square authorization expired or was already used")
        environment = attempt.environment
        session.commit()
        if request.query_params.get("error") or not request.query_params.get("code"):
            return finish(request, "Square authorization was cancelled. You can connect again.")
        try:
            provider = SquareProvider(SquareConfig.load(environment))
            tokens = asyncio.run(provider.exchange(request.query_params["code"]))
            account_id = tokens["merchant_id"]
            scopes = asyncio.run(provider.scopes(tokens["access_token"]))
            connection = session.scalar(select(IntegrationConnection).where(
                IntegrationConnection.provider == "square", IntegrationConnection.environment == environment,
                IntegrationConnection.external_account_id == account_id).with_for_update())
            if connection and connection.organization_id != org_id:
                return finish(request, "This Square account is already linked to another business. Disconnect it there first.")
            if not connection:
                connection = IntegrationConnection(organization_id=org_id, provider="square", environment=environment, external_account_id=account_id)
                session.add(connection)
            save_tokens(connection, tokens)
            connection.granted_scopes = scopes
            session.commit()
        except (SquareError, RuntimeError, ValueError, KeyError, IntegrityError):
            session.rollback()
            return finish(request, "Square connection failed. Check configuration and try connecting again.")
    return finish(request, "Square connected. Map your locations and employees below.")


@router.post("/{connection_id}/mapping")
def mapping(request: Request, connection_id: int, kind: str = Form(...), external_id: str = Form(...), target_id: int = Form(...), csrf_token: str = Form(...)):
    _, org_id = authorize(request)
    csrf(request, csrf_token)
    with SessionLocal() as session:
        connection = owned(session, org_id, connection_id)
        try:
            locations, employees = asyncio.run(directory(session, connection))
            choices = locations if kind == "location" else employees if kind == "employee" else []
            if external_id not in {item["id"] for item in choices}:
                raise ValueError("Select a valid Square record")
            if target_id == 0:
                row = session.scalar(select(IntegrationMapping).where(IntegrationMapping.connection_id == connection.id,
                    IntegrationMapping.kind == kind, IntegrationMapping.external_id == external_id))
                if row:
                    session.delete(row)
            else:
                set_mapping(session, connection, kind, external_id, target_id)
            session.commit()
        except (SquareError, ValueError, RuntimeError, InvalidToken, IntegrityError):
            session.rollback()
            return finish(request, "Mapping could not be saved. Verify the connection and choose a record that is not already mapped.")
    return finish(request, "Mapping saved.")


@router.post("/{connection_id}/disconnect")
def disconnect(request: Request, connection_id: int, csrf_token: str = Form(...)):
    _, org_id = authorize(request)
    csrf(request, csrf_token)
    with SessionLocal() as session:
        connection = owned(session, org_id, connection_id)
        try:
            if connection.access_token_encrypted:
                asyncio.run(SquareProvider(SquareConfig.load(connection.environment)).revoke(decrypt(connection.access_token_encrypted)))
        except (SquareError, RuntimeError, ValueError, InvalidToken):
            return finish(request, "Square revocation could not be confirmed. Revoke access in Square My Applications, then retry here.")
        connection.access_token_encrypted = None
        connection.refresh_token_encrypted = None
        connection.granted_scopes = []
        connection.status = "disconnected"
        session.commit()
    return finish(request, "Square disconnected. Saved mappings are retained for reconnecting the same account.")
