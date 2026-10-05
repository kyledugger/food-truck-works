import base64
import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from datetime import timedelta
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database import SessionLocal
from models import User, PoyntConnection, OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_integrations
from live_dashboard_models import DashboardOrder, DashboardSync, DashboardNotification
from live_dashboard_metrics import summarize
from live_dashboard_service import ensure_sync, acquire, owned_state, release
from poynt.client import PoyntClient, PoyntAPIError
from poynt.connection import get_poynt_credentials
from store_time import local_day_bounds, utc_iso, utc_now

router = APIRouter()


def authorized(request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(401, "Sign in to view the dashboard.")
    with SessionLocal() as session:
        if session.get(User, user_id) is None:
            request.session.clear()
            raise HTTPException(401, "Sign in to view the dashboard.")
    organization_id = get_current_organization_id(request)
    role = get_organization_role(user_id, organization_id) if organization_id else None
    if role is None:
        raise HTTPException(403, "Choose a business you belong to.")
    return organization_id, role


@router.get("/dashboard/data")
def dashboard_data(request: Request):
    organization_id, role = authorized(request)
    now = utc_now()
    try:
        with SessionLocal() as session:
            connection = session.scalar(select(PoyntConnection).where(PoyntConnection.organization_id == organization_id))
            if connection is None:
                return JSONResponse({"connected": False, "stores": [], "generated_at": utc_iso(now)}, headers={"Cache-Control": "no-store"})
            state = ensure_sync(session, organization_id, connection.business_id)
            session.commit()
            stores = session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id == organization_id,
                OrganizationStore.is_active.is_(True)).order_by(OrganizationStore.id)).all()
            results = []
            needs_day_sync = False
            for store in stores:
                try:
                    zone = ZoneInfo(store.timezone_name or "")
                    start, end = local_day_bounds(now.astimezone(zone).date(), zone)
                except (ValueError, KeyError):
                    results.append({"id": store.id, "name": store.display_name or store.poynt_name,
                        "setup_required": True, "message": "Set this store's timezone in Store Settings."})
                    continue
                if state.last_reconciled_at is None or state.last_reconciled_at < start:
                    needs_day_sync = True
                orders = session.scalars(select(DashboardOrder).where(DashboardOrder.organization_id == organization_id,
                    DashboardOrder.business_id == connection.business_id, DashboardOrder.store_id == store.store_id,
                    DashboardOrder.created_at >= start - timedelta(minutes=65), DashboardOrder.created_at < end)).all()
                results.append(summarize(store, [row.payload for row in orders], now))
            if needs_day_sync and state.next_reconcile_at > now:
                state.next_reconcile_at = now
                session.commit()
            pending = session.scalar(select(DashboardNotification.received_at).where(
                DashboardNotification.organization_id == organization_id,
                DashboardNotification.business_id == connection.business_id,
                DashboardNotification.processed_at.is_(None)).order_by(DashboardNotification.received_at).limit(1))
            data = {"connected": True, "generated_at": utc_iso(now), "stores": results,
                "last_reconciled_at": utc_iso(state.last_reconciled_at), "last_webhook_at": utc_iso(state.last_webhook_at),
                "initializing": state.last_reconciled_at is None or needs_day_sync,
                "stale": needs_day_sync or state.last_reconciled_at is None or state.last_reconciled_at < now - timedelta(minutes=7),
                "error": state.error,
                "queue_delayed": bool(pending and pending < now - timedelta(seconds=60)),
                "webhook_registered": bool(state.hook_id),
                "can_enable_webhook": role_can_manage_integrations(role) and bool(os.getenv("POYNT_WEBHOOK_SECRET") and os.getenv("POYNT_WEBHOOK_URL"))}
            return JSONResponse(data, headers={"Cache-Control": "no-store"})
    except SQLAlchemyError:
        return JSONResponse({"detail": "Dashboard storage is unavailable. Apply the live dashboard migration and retry."}, status_code=503,
                            headers={"Cache-Control": "no-store"})


@router.post("/dashboard/webhook/enable")
async def enable_webhook(request: Request):
    organization_id, role = authorized(request)
    if not role_can_manage_integrations(role):
        raise HTTPException(403, "Only integration managers can enable live updates.")
    # AJAX-only + same-origin fetch protection for cookie-authenticated mutation.
    if request.headers.get("x-requested-with") != "FoodTruckWorks" or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Use the dashboard to enable live updates.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Invalid request origin.")
    secret, url = os.getenv("POYNT_WEBHOOK_SECRET"), os.getenv("POYNT_WEBHOOK_URL")
    if not secret or len(secret) < 32 or not url or urlsplit(url).scheme != "https" or urlsplit(url).path != "/webhooks/poynt/orders":
        raise HTTPException(400, "Configure POYNT_WEBHOOK_SECRET (at least 32 characters) and the HTTPS POYNT_WEBHOOK_URL first.")
    credentials = get_poynt_credentials(organization_id)
    if not credentials:
        raise HTTPException(400, "Connect Poynt first.")
    with SessionLocal() as session:
        state = ensure_sync(session, organization_id, credentials.business_id)
        registered = bool(state.hook_id)
        session.commit()
    if registered:
        return {"registered": True}
    claim = acquire(organization_id)
    if claim is None:
        raise HTTPException(409, "An order sync is running. Try again shortly.")
    business_id, token = claim
    try:
        with SessionLocal() as session:
            state = owned_state(session, organization_id, business_id, token)
            if state and state.hook_id:
                return {"registered": True}
        client = PoyntClient(credentials, organization_id)
        result = await client.register_order_webhook(url, secret)
        if not result.get("id"):
            raise PoyntAPIError("Missing webhook ID")
        with SessionLocal() as session:
            state = owned_state(session, organization_id, business_id, token)
            if not state:
                raise HTTPException(409, "The Poynt connection changed. Refresh the dashboard.")
            state.hook_id = str(result["id"])
            session.commit()
        return {"registered": True}
    except PoyntAPIError:
        raise HTTPException(502, "Poynt webhook registration failed. Check the Poynt connection and callback configuration.")
    finally:
        release(organization_id, token)


@router.post("/webhooks/poynt/orders", include_in_schema=False)
async def order_webhook(request: Request):
    secret = os.getenv("POYNT_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "Webhook is not configured.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 65536:
            raise HTTPException(413, "Notification too large.")
    signature = base64.b64encode(hmac.new(secret.encode(), bytes(body), hashlib.sha1).digest()).decode()
    if not hmac.compare_digest(signature, request.headers.get("poynt-webhook-signature", "")):
        raise HTTPException(401, "Invalid webhook signature.")
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError()
        for field in ("id", "businessId", "resourceId", "hookId"):
            if not isinstance(data.get(field), str) or not 1 <= len(data[field]) <= 100:
                raise ValueError()
        if data.get("applicationId") != os.getenv("POYNT_APP_ID"):
            raise ValueError()
        if data.get("resource") != "/orders" or data.get("eventType") not in {"ORDER_OPENED", "ORDER_COMPLETED", "ORDER_CANCELLED", "ORDER_UPDATED"}:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid order notification.")
    with SessionLocal() as session:
        connections = session.scalars(select(PoyntConnection).where(PoyntConnection.business_id == data["businessId"])).all()
        if len(connections) != 1:
            raise HTTPException(409, "Poynt business must belong to exactly one organization.")
        connection = connections[0]
        state = ensure_sync(session, connection.organization_id, connection.business_id)
        if state.hook_id and state.hook_id != data["hookId"]:
            raise HTTPException(403, "Unexpected webhook registration.")
        exists = session.scalar(select(DashboardNotification.id).where(
            DashboardNotification.organization_id == connection.organization_id,
            DashboardNotification.business_id == connection.business_id, DashboardNotification.notification_id == data["id"]))
        if not exists:
            try:
                with session.begin_nested():
                    session.add(DashboardNotification(organization_id=connection.organization_id,
                        business_id=connection.business_id, notification_id=data["id"], order_id=data["resourceId"]))
                    session.flush()
            except IntegrityError:
                pass  # Concurrent duplicate delivery is already durably stored.
        state.last_webhook_at = utc_now()
        session.commit()
    return {"accepted": True}
