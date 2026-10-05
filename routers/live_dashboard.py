import base64
import hashlib
import hmac
import json
import os
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from datetime import timedelta, date as calendar_date
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database import SessionLocal
from models import User, PoyntConnection, OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_integrations
from live_dashboard_models import DashboardOrder, DashboardSync, DashboardNotification, DashboardDay
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
def dashboard_data(request: Request, report_date: str = Query("today", alias="date")):
    organization_id, role = authorized(request)
    now = utc_now()
    try:
        selected_date = calendar_date.fromisoformat(report_date) if report_date not in {"today", "yesterday"} else None
        if selected_date and selected_date.isoformat() != report_date:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Choose Today, Yesterday, or a date in YYYY-MM-DD format.")
    try:
        with SessionLocal() as session:
            connection = session.scalar(select(PoyntConnection).where(PoyntConnection.organization_id == organization_id))
            if connection is None:
                return JSONResponse({"connected": False, "stores": [], "generated_at": utc_iso(now),
                    "selection": report_date, "polling_needed": False}, headers={"Cache-Control": "no-store"})
            state = ensure_sync(session, organization_id, connection.business_id)
            session.commit()
            stores = session.scalars(select(OrganizationStore).where(OrganizationStore.organization_id == organization_id,
                OrganizationStore.is_active.is_(True)).order_by(OrganizationStore.id)).all()
            results = []
            live_pending = False
            history_pending = False
            view_load_times = []
            local_dates = []
            has_live_store = False
            history_errors = []
            for store in stores:
                try:
                    zone = ZoneInfo(store.timezone_name or "")
                    local_today = now.astimezone(zone).date()
                    day = selected_date or (local_today - timedelta(days=1) if report_date == "yesterday" else local_today)
                    if not local_today - timedelta(days=89) <= day <= local_today:
                        raise HTTPException(400, "Choose a date within the last 90 days, including today, in each store's timezone.")
                    start, end = local_day_bounds(day, zone)
                    local_dates.append(local_today)
                except (ValueError, KeyError):
                    results.append({"id": store.id, "name": store.display_name or store.poynt_name,
                        "setup_required": True, "message": "Set this store's timezone in Store Settings."})
                    continue
                historical = day < local_today
                load_error = None
                if historical:
                    cache = session.scalar(select(DashboardDay).where(DashboardDay.organization_id == organization_id,
                        DashboardDay.business_id == connection.business_id, DashboardDay.report_date == day))
                    if cache is None:
                        try:
                            with session.begin_nested():
                                cache = DashboardDay(organization_id=organization_id, business_id=connection.business_id, report_date=day)
                                session.add(cache)
                                session.flush()
                        except IntegrityError:
                            cache = session.scalar(select(DashboardDay).where(DashboardDay.organization_id == organization_id,
                                DashboardDay.business_id == connection.business_id, DashboardDay.report_date == day))
                    cache.requested_at = now
                    loading = cache.loaded_at is None
                    stale = loading or cache.loaded_at < now - timedelta(minutes=5)
                    history_pending |= stale
                    load_error = cache.error
                    if cache.error:
                        history_errors.append(cache.error)
                    if cache.loaded_at:
                        view_load_times.append(cache.loaded_at)
                else:
                    has_live_store = True
                    loading = state.last_reconciled_at is None or state.last_reconciled_at < start
                    live_pending |= loading
                    stale = loading or state.last_reconciled_at < now - timedelta(minutes=7)
                    if state.last_reconciled_at:
                        view_load_times.append(state.last_reconciled_at)
                orders = session.scalars(select(DashboardOrder).where(DashboardOrder.organization_id == organization_id,
                    DashboardOrder.business_id == connection.business_id, DashboardOrder.store_id == store.store_id,
                    DashboardOrder.created_at >= start - (timedelta(0) if historical else timedelta(minutes=65)),
                    DashboardOrder.created_at < end)).all()
                payloads = [row.payload for row in orders]
                result = summarize(store, payloads, end - timedelta(microseconds=1) if historical else now, historical=historical)
                result.update(loading=loading, stale=stale, load_error=load_error)
                results.append(result)
            if live_pending and state.next_reconcile_at > now:
                state.next_reconcile_at = now
            session.commit()
            pending = session.scalar(select(DashboardNotification.received_at).where(
                DashboardNotification.organization_id == organization_id,
                DashboardNotification.business_id == connection.business_id,
                DashboardNotification.processed_at.is_(None)).order_by(DashboardNotification.received_at).limit(1))
            data = {"connected": True, "generated_at": utc_iso(now), "stores": results,
                "selection": report_date, "historical_view": not has_live_store and bool(local_dates),
                "polling_needed": has_live_store or history_pending,
                "earliest_date": (max(local_dates) - timedelta(days=89)).isoformat() if local_dates else None,
                "latest_date": min(local_dates).isoformat() if local_dates else None,
                "last_view_loaded_at": utc_iso(min(view_load_times)) if view_load_times else None,
                "last_reconciled_at": utc_iso(state.last_reconciled_at), "last_webhook_at": utc_iso(state.last_webhook_at),
                "initializing": any(s.get("loading") for s in results),
                "stale": any(s.get("stale") for s in results),
                "error": history_errors[0] if history_errors else state.error if has_live_store else None,
                "queue_delayed": bool(has_live_store and pending and pending < now - timedelta(seconds=60)),
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
