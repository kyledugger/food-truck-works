"""Store-scoped kitchen queue and short-lived, reauthorized SSE connections."""
import asyncio
import secrets
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, String, delete
from sqlalchemy.exc import SQLAlchemyError
from database import SessionLocal
from models import OrganizationStore, PoyntConnection
from live_dashboard_models import DashboardOrder
from kitchen_models import KitchenTicket, KitchenAction, KitchenTimerProfile, KitchenSession
from kitchen_service import sync_ticket, transition, serialize, edit_note
from live_dashboard_metrics import kitchen_intake, instant
from store_time import utc_now, utc_iso, local_day_bounds

router = APIRouter()


def completion_rate(tickets, now, minutes=15, since=None):
    """Count current done quantities once; undo removes them from the window."""
    total = 0.0
    for ticket in tickets:
        if ticket.state == "cancelled":
            continue
        for item in ticket.items:
            if item.get("state") != "done" or not item.get("done_at"):
                continue
            at = instant(item["done_at"])
            if max(now-timedelta(minutes=minutes), since or now-timedelta(minutes=minutes)) <= at <= now:
                total += max(0, float(item.get("quantity", 0)))
    return {"completed_items": total, "items_per_minute": total/minutes}


def customer_wait(tickets, now):
    eligible = [t for t in tickets if t.state == "active" and any(i.get("state") != "done" for i in t.items)]
    oldest = min(eligible, key=lambda t: (t.created_at, t.id)) if eligible else None
    return {"ticket_id": oldest.id, "created_at": utc_iso(oldest.created_at),
            "wait_seconds": max(0, int((now-oldest.created_at).total_seconds()))} if oldest else None


def scope(request, session, store_id):
    from routers.live_dashboard import sales_authorized
    organization_id, role = sales_authorized(request, store_id)
    store = session.scalar(select(OrganizationStore).where(OrganizationStore.id == store_id,
        OrganizationStore.organization_id == organization_id, OrganizationStore.is_active.is_(True)))
    if store is None:
        raise HTTPException(404, "Store not found.")
    if "/kitchen-test" in getattr(getattr(request, "url", None), "path", ""):
        return store, SimpleNamespace(business_id=f"kitchen-test:{store.id}")
    connection = session.scalar(select(PoyntConnection).where(PoyntConnection.organization_id == organization_id))
    if connection is None:
        raise HTTPException(409, "Connect Poynt to receive kitchen orders.")
    return store, connection


def filters(store, connection):
    return (KitchenTicket.organization_id == store.organization_id, KitchenTicket.store_id == store.id,
            KitchenTicket.business_id == connection.business_id)


@router.get("/dashboard/stores/{store_id}/kitchen")
@router.get("/dashboard/stores/{store_id}/kitchen-test")
def queue(request: Request, store_id: int):
    now = utc_now()
    try:
        with SessionLocal() as session:
            store, connection = scope(request, session, store_id)
            simulated = "/kitchen-test" in request.url.path
            tracking = session.get(KitchenSession, store.id)
            tracking_on = simulated or bool(tracking and tracking.active and tracking.business_id == connection.business_id)
            needs_resume = bool(not simulated and tracking_on and tracking.last_seen_at and tracking.last_seen_at < now-timedelta(minutes=15))
            if not simulated and tracking_on and not needs_resume:
                tracking.last_seen_at = now
            session_info = {"active": tracking_on, "needs_resume": needs_resume,
                "started_at": utc_iso(tracking.started_at) if tracking and tracking_on else None,
                "revision": tracking.revision if tracking else 0}
            if not store.timezone_name:
                raise HTTPException(409, "Configure the store timezone first.")
            start, end = local_day_bounds(now.astimezone(ZoneInfo(store.timezone_name)).date(), ZoneInfo(store.timezone_name))
            # Seed today's existing cache on first use; prior-day tickets already
            # admitted remain active until prepared, even across midnight.
            admitted = select(KitchenTicket.id).where(KitchenTicket.organization_id == DashboardOrder.organization_id,
                KitchenTicket.business_id == DashboardOrder.business_id, KitchenTicket.order_id == DashboardOrder.order_id).exists()
            orders = session.scalars(select(DashboardOrder).where(~admitted, DashboardOrder.organization_id == store.organization_id,
                DashboardOrder.business_id == connection.business_id, DashboardOrder.store_id == store.store_id,
                DashboardOrder.created_at >= start, DashboardOrder.created_at < end,
                DashboardOrder.created_at >= (tracking.started_at if tracking and not simulated and tracking_on else start),
                DashboardOrder.created_at <= now)).all() if tracking_on and not needs_resume else []
            for order in orders:
                sync_ticket(session, order, now)
            session.flush()
            active = session.scalars(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.state.in_(["active", "held"] if tracking_on and not needs_resume else []))
                .order_by(KitchenTicket.created_at, KitchenTicket.id)).all()
            recent = session.scalars(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.state == "ready",
                KitchenTicket.ready_at >= now-timedelta(hours=2)).order_by(KitchenTicket.ready_at.desc()).limit(20)).all()
            flow_orders = session.scalars(select(DashboardOrder).where(
                DashboardOrder.organization_id == store.organization_id,
                DashboardOrder.business_id == connection.business_id, DashboardOrder.store_id == store.store_id,
                DashboardOrder.created_at >= now-timedelta(minutes=30), DashboardOrder.created_at <= now)).all()
            flow_payloads = [o.payload for o in flow_orders]
            if "/kitchen-test" in request.url.path:
                samples = session.scalars(select(KitchenTicket).where(*filters(store, connection),
                    KitchenTicket.state != "cancelled", KitchenTicket.created_at >= now-timedelta(minutes=30),
                    KitchenTicket.created_at <= now)).all()
                flow_payloads = [{"createdAt": utc_iso(t.created_at), "statuses": {"status": "COMPLETED", "transactionStatusSummary": "COMPLETED"}, "items": t.items} for t in samples]
            completed_tickets = session.scalars(select(KitchenTicket).where(*filters(store, connection),
                KitchenTicket.state.in_(["active", "held", "ready"]), KitchenTicket.updated_at >= now-timedelta(minutes=15))).all()
            if not tracking_on or needs_resume:
                active, recent, completed_tickets = [], [], []
            elif tracking and not simulated:
                recent = [t for t in recent if t.ready_at >= tracking.started_at]
            result = {"active": [serialize(t) for t in active], "recent": [serialize(t) for t in recent],
                      "session": session_info,
                      "timer_profile": timer_profile(session, store.id),
                      "generated_at": utc_iso(now), "timezone": store.timezone_name,
                      "flow": {"window_minutes": 15,
                               "customer_wait": customer_wait(active, now),
                               "intake": kitchen_intake(flow_payloads, now, 15),
                               "tracking": tracking_on and not needs_resume,
                               "completion": completion_rate(completed_tickets, now, since=tracking.started_at if tracking and not simulated else None) if tracking_on and not needs_resume else None}}
            session.commit()
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except SQLAlchemyError:
        raise HTTPException(503, "Kitchen queue unavailable. Check the kitchen migration and database.")


def check_csrf(request):
    csrf = request.session.get("kitchen_csrf")
    supplied = request.headers.get("x-kitchen-csrf", "")
    if not csrf or not secrets.compare_digest(csrf, supplied):
        raise HTTPException(403, "Reload the store display and try again.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Invalid request origin.")


class ServiceAction(BaseModel):
    action: Literal["start", "resume", "end", "clear", "recent", "import", "archived"]
    revision: int = Field(ge=0)
    minutes: Literal[10, 30, 60] = 10
    order_ids: list[str] = Field(default_factory=list, max_length=100)


def archive_queue(session, store, connection, actor):
    for ticket in session.scalars(select(KitchenTicket).where(*filters(store, connection),
        KitchenTicket.state.in_(["active", "held"])).with_for_update()).all():
        ticket.state = "archived"
        ticket.revision += 1
        ticket.updated_at = utc_now()
        session.add(KitchenAction(ticket_id=ticket.id, action="archived", revision=ticket.revision,
            actor_user_id=actor, details={"reason": "cleared_without_tracking"}))


@router.post("/dashboard/stores/{store_id}/kitchen/service")
def service(request: Request, store_id: int, body: ServiceAction):
    check_csrf(request)
    now = utc_now()
    with SessionLocal() as session:
        store, connection = scope(request, session, store_id)
        # Serialize session creation and lifecycle changes with order admission.
        session.scalar(select(OrganizationStore).where(OrganizationStore.id == store.id).with_for_update())
        tracking = session.scalar(select(KitchenSession).where(KitchenSession.store_id == store.id).with_for_update())
        if body.revision != (tracking.revision if tracking else 0):
            raise HTTPException(409, "Kitchen session changed on another screen. Refresh and try again.")
        if body.action == "archived":
            tickets = session.scalars(select(KitchenTicket).where(*filters(store, connection),
                KitchenTicket.state == "archived").order_by(KitchenTicket.updated_at.desc()).limit(100)).all()
            return {"tickets": [serialize(t) for t in tickets]}
        if tracking is None:
            tracking = KitchenSession(store_id=store.id, business_id=connection.business_id, active=False, revision=0)
            session.add(tracking)
        if body.action in {"recent", "import", "resume", "clear"} and (not tracking.active or tracking.business_id != connection.business_id):
            raise HTTPException(409, "Start kitchen service first.")
        if body.action in {"start", "end", "clear"}:
            archive_queue(session, store, connection, request.session["user_id"])
        if body.action == "start":
            tracking.business_id = connection.business_id
            tracking.started_at = now
            tracking.last_seen_at = now
            tracking.active = True
        elif body.action == "end":
            tracking.active = False
        elif body.action == "resume":
            tracking.last_seen_at = now
        if body.action in {"start", "resume"}:
            from live_dashboard_service import ensure_sync
            ensure_sync(session, store.organization_id, connection.business_id).next_reconcile_at = now
        if body.action in {"recent", "import"}:
            exists = select(KitchenTicket.id).where(KitchenTicket.organization_id == store.organization_id,
                KitchenTicket.business_id == connection.business_id, KitchenTicket.order_id == DashboardOrder.order_id).exists()
            candidates = session.scalars(select(DashboardOrder).where(
                DashboardOrder.organization_id == store.organization_id, DashboardOrder.business_id == connection.business_id,
                DashboardOrder.store_id == store.store_id, DashboardOrder.created_at >= tracking.started_at-timedelta(minutes=body.minutes),
                DashboardOrder.created_at < tracking.started_at, ~exists).order_by(DashboardOrder.created_at).limit(100)).all()
            from live_dashboard_metrics import completed
            from kitchen_service import lines
            candidates = [o for o in candidates if completed(o.payload) and lines(o.payload)]
            if body.action == "recent":
                return {"orders": [{"id": o.order_id, "number": o.payload.get("orderNumber") or o.order_id,
                    "created_at": utc_iso(o.created_at), "items": [i["name"] for i in lines(o.payload)]} for o in candidates],
                    "revision": tracking.revision}
            requested = set(body.order_ids)
            if not requested.issubset({o.order_id for o in candidates}):
                raise HTTPException(409, "Some orders are no longer eligible. Search again.")
            for order in candidates:
                if order.order_id in requested:
                    sync_ticket(session, order, now, import_recent=True)
        tracking.revision += 1
        session.commit()
    return {"ok": True}


@router.post("/dashboard/stores/{store_id}/kitchen/refresh")
async def refresh_orders(request: Request, store_id: int):
    """Wait for a provider check, sharing the worker's organization lease."""
    from live_dashboard_service import ensure_sync, acquire, release, process_organization, owned_state
    from live_dashboard_models import DashboardSync
    check_csrf(request)
    requested_at = utc_now()
    with SessionLocal() as session:
        store, connection = scope(request, session, store_id)
        if not store.timezone_name:
            raise HTTPException(409, "Configure the store timezone first.")
        organization_id, business_id = store.organization_id, connection.business_id
        state = ensure_sync(session, organization_id, business_id)
        session.commit()
    deadline = asyncio.get_running_loop().time() + 55
    while asyncio.get_running_loop().time() < deadline:
        with SessionLocal() as session:
            scope(request, session, store_id)
            state = session.get(DashboardSync, organization_id)
            if state and state.business_id == business_id and state.last_reconciled_at and state.last_reconciled_at >= requested_at:
                return queue(request, store_id)
        claim = acquire(organization_id)
        if claim is None:
            await asyncio.sleep(.25)
            continue
        claimed_business, token = claim
        try:
            if claimed_business != business_id:
                raise HTTPException(409, "Poynt connection changed. Reload the page.")
            remaining = deadline - asyncio.get_running_loop().time()
            await asyncio.wait_for(process_organization(organization_id, business_id, token, refresh_only=True), timeout=max(.1, remaining))
            return queue(request, store_id)
        except HTTPException:
            raise
        except Exception:
            with SessionLocal() as session:
                state = owned_state(session, organization_id, business_id, token)
                if state:
                    state.error = "Order sync failed. Retrying shortly."
                    state.next_reconcile_at = utc_now() + timedelta(seconds=60)
                    session.commit()
            raise HTTPException(502, "Poynt refresh failed or timed out. Try again shortly.") from None
        finally:
            release(organization_id, token)
    raise HTTPException(503, "An order sync is still running. Try Refresh again shortly.")


def timer_profile(session, store_id):
    profile = session.get(KitchenTimerProfile, store_id)
    return {name: getattr(profile, name) if profile else default for name, default in
        [("green_seconds", 0), ("yellow_seconds", 300), ("red_seconds", 600)]}


class TimerProfile(BaseModel):
    green_seconds: int = Field(ge=0, le=86400)
    yellow_seconds: int = Field(ge=0, le=86400)
    red_seconds: int = Field(ge=0, le=86400)


@router.post("/dashboard/stores/{store_id}/kitchen-profile")
def save_timer_profile(request: Request, store_id: int, body: TimerProfile):
    check_csrf(request)
    if not body.green_seconds < body.yellow_seconds < body.red_seconds:
        raise HTTPException(422, "Thresholds must increase: green, yellow, then red.")
    from routers.live_dashboard import sales_authorized
    organization_id, role = sales_authorized(request, store_id)
    if role not in {"owner", "manager"}:
        raise HTTPException(403, "An owner or manager must change the timer profile.")
    with SessionLocal() as session:
        store = session.scalar(select(OrganizationStore).where(OrganizationStore.id == store_id,
            OrganizationStore.organization_id == organization_id, OrganizationStore.is_active.is_(True)))
        if store is None:
            raise HTTPException(404, "Store not found.")
        profile = session.get(KitchenTimerProfile, store_id)
        if profile is None:
            profile = KitchenTimerProfile(store_id=store_id)
            session.add(profile)
        for name, value in body.model_dump().items():
            setattr(profile, name, value)
        session.commit()
    return JSONResponse(body.model_dump(), headers={"Cache-Control": "no-store"})


class TestOrders(BaseModel):
    action: Literal["add", "clear"]
    count: int = Field(default=1, ge=1, le=25)
    scenario: Literal["mixed", "large"] = "mixed"


@router.post("/dashboard/stores/{store_id}/kitchen-test/orders")
def test_orders(request: Request, store_id: int, body: TestOrders):
    check_csrf(request)
    from kitchen_simulation import add_samples
    with SessionLocal() as session:
        store, connection = scope(request, session, store_id)
        if body.action == "clear":
            ids = select(KitchenTicket.id).where(*filters(store, connection))
            session.execute(delete(KitchenAction).where(KitchenAction.ticket_id.in_(ids)))
            session.execute(delete(KitchenTicket).where(*filters(store, connection)))
        else:
            active = session.scalar(select(func.count(KitchenTicket.id)).where(*filters(store, connection), KitchenTicket.state.in_(["active", "held"])))
            if active + body.count > 100:
                raise HTTPException(409, "Test queue is full. Complete or clear orders first.")
            add_samples(session, store, connection.business_id, body.count, body.scenario, request.session["user_id"])
        session.commit()
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


class Action(BaseModel):
    action: Literal["claim", "done", "release", "undo", "hold", "resume", "edit_note"]
    revision: int = Field(ge=1)
    item_key: str | None = Field(default=None, min_length=64, max_length=64)
    notes: str | None = Field(default=None, max_length=4000)
    customer_name: str | None = Field(default=None, max_length=200)


@router.post("/dashboard/stores/{store_id}/kitchen/{ticket_id}")
@router.post("/dashboard/stores/{store_id}/kitchen-test/{ticket_id}")
def act(request: Request, store_id: int, ticket_id: int, body: Action):
    check_csrf(request)
    try:
        with SessionLocal() as session:
            store, connection = scope(request, session, store_id)
            ticket = session.scalar(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.id == ticket_id))
            if "/kitchen-test" not in request.url.path:
                tracking = session.scalar(select(KitchenSession).where(KitchenSession.store_id == store.id).with_for_update())
                if not tracking or not tracking.active or tracking.business_id != connection.business_id:
                    raise HTTPException(409, "Kitchen tracking is off. Start service first.")
                if ticket and ticket.state == "ready" and ticket.ready_at and ticket.ready_at < tracking.started_at:
                    raise HTTPException(409, "This ticket belongs to an earlier kitchen service.")
            if ticket is None:
                raise HTTPException(404, "Order not found in this store.")
            # Refresh the latest cached POS payload under the same row lock as
            # sync. A stale browser must not complete newly added/changed items.
            order = session.scalar(select(DashboardOrder).where(DashboardOrder.organization_id == store.organization_id,
                DashboardOrder.business_id == connection.business_id, DashboardOrder.order_id == ticket.order_id))
            if order:
                sync_ticket(session, order)
                session.flush()
            if ticket.revision != body.revision:
                session.commit()
                raise HTTPException(409, "Order changed. Review its current items before acting.")
            if body.action == "edit_note":
                if body.item_key is not None or body.notes is None or body.customer_name is None:
                    raise HTTPException(422, "Provide an order note and customer name.")
                revision = edit_note(session, ticket, body.notes.strip(), body.customer_name.strip(), body.revision, request.session["user_id"])
            else:
                revision = transition(session, ticket, body.action, body.item_key, body.revision, request.session["user_id"])
            session.commit()
            session.refresh(ticket)
            saved = serialize(ticket)
        return JSONResponse({"revision": revision, "ticket": saved, "generated_at": utc_iso(utc_now())}, headers={"Cache-Control": "no-store"})
    except SQLAlchemyError:
        raise HTTPException(503, "Kitchen action could not be saved. Refresh before trying again.")


def stamp(request, store_id):
    # Durable database state lets every Uvicorn process observe committed changes.
    # No connection is held while waiting on the network or sleeping.
    with SessionLocal() as session:
        store, connection = scope(request, session, store_id)
        tracking = session.get(KitchenSession, store.id)
        return tuple(session.execute(select(func.count(KitchenTicket.id), func.coalesce(func.sum(KitchenTicket.revision), 0),
            func.coalesce(func.max(KitchenTicket.id), 0), func.max(KitchenTicket.updated_at).cast(String)).where(*filters(store, connection))).one()) + tuple(timer_profile(session, store.id).values()) + (tracking.revision if tracking else 0,)


@router.get("/dashboard/stores/{store_id}/kitchen/events")
@router.get("/dashboard/stores/{store_id}/kitchen-test/events")
async def events(request: Request, store_id: int):
    try:
        await asyncio.to_thread(stamp, request, store_id)
    except SQLAlchemyError:
        raise HTTPException(503, "Kitchen live updates unavailable.")
    async def stream():
        previous = None
        yield "retry: 2000\n\n"
        # Reconnect periodically so signed-cookie expiration is checked anew.
        for tick in range(60):
            if await request.is_disconnected():
                return
            try:
                current = await asyncio.to_thread(stamp, request, store_id)
            except HTTPException:
                yield 'event: access-denied\ndata: {}\n\n'
                return
            except SQLAlchemyError:
                return  # EventSource reconnect + polling fallback.
            if current != previous:
                previous = current
                yield 'event: queue-changed\ndata: {}\n\n'
            elif tick % 10 == 0:
                yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
