"""Store-scoped kitchen queue and short-lived, reauthorized SSE connections."""
import asyncio
import secrets
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func, String
from sqlalchemy.exc import SQLAlchemyError
from database import SessionLocal
from models import OrganizationStore, PoyntConnection
from live_dashboard_models import DashboardOrder
from kitchen_models import KitchenTicket
from kitchen_service import sync_ticket, transition, serialize
from live_dashboard_metrics import kitchen_intake, instant
from store_time import utc_now, utc_iso, local_day_bounds

router = APIRouter()


def completion_rate(tickets, now, minutes=15):
    """Count current done quantities once; undo removes them from the window."""
    total = 0.0
    for ticket in tickets:
        if ticket.state == "cancelled":
            continue
        for item in ticket.items:
            if item.get("state") != "done" or not item.get("done_at"):
                continue
            at = instant(item["done_at"])
            if now-timedelta(minutes=minutes) <= at <= now:
                total += max(0, float(item.get("quantity", 0)))
    return {"completed_items": total, "items_per_minute": total/minutes}


def scope(request, session, store_id):
    from routers.live_dashboard import sales_authorized
    organization_id, role = sales_authorized(request, store_id)
    if role == "store_display" and request.session.get("store_performance_id") != store_id:
        raise HTTPException(403, "Open Store Performance from the store home screen first.")
    store = session.scalar(select(OrganizationStore).where(OrganizationStore.id == store_id,
        OrganizationStore.organization_id == organization_id, OrganizationStore.is_active.is_(True)))
    if store is None:
        raise HTTPException(404, "Store not found.")
    connection = session.scalar(select(PoyntConnection).where(PoyntConnection.organization_id == organization_id))
    if connection is None:
        raise HTTPException(409, "Connect Poynt to receive kitchen orders.")
    return store, connection


def filters(store, connection):
    return (KitchenTicket.organization_id == store.organization_id, KitchenTicket.store_id == store.id,
            KitchenTicket.business_id == connection.business_id)


@router.get("/dashboard/stores/{store_id}/kitchen")
def queue(request: Request, store_id: int):
    now = utc_now()
    try:
        with SessionLocal() as session:
            store, connection = scope(request, session, store_id)
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
                DashboardOrder.created_at <= now)).all()
            for order in orders:
                sync_ticket(session, order, now)
            session.flush()
            active = session.scalars(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.state == "active")
                .order_by(KitchenTicket.created_at, KitchenTicket.id)).all()
            recent = session.scalars(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.state == "ready",
                KitchenTicket.ready_at >= now-timedelta(hours=2)).order_by(KitchenTicket.ready_at.desc()).limit(20)).all()
            flow_orders = session.scalars(select(DashboardOrder).where(
                DashboardOrder.organization_id == store.organization_id,
                DashboardOrder.business_id == connection.business_id, DashboardOrder.store_id == store.store_id,
                DashboardOrder.created_at >= now-timedelta(minutes=30), DashboardOrder.created_at <= now)).all()
            completed_tickets = session.scalars(select(KitchenTicket).where(*filters(store, connection),
                KitchenTicket.state.in_(["active", "ready"]), KitchenTicket.updated_at >= now-timedelta(minutes=15))).all()
            result = {"active": [serialize(t) for t in active], "recent": [serialize(t) for t in recent],
                      "generated_at": utc_iso(now), "timezone": store.timezone_name,
                      "flow": {"window_minutes": 15,
                               "intake": kitchen_intake([o.payload for o in flow_orders], now, 15),
                               "completion": completion_rate(completed_tickets, now)}}
            session.commit()
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except SQLAlchemyError:
        raise HTTPException(503, "Kitchen queue unavailable. Check the kitchen migration and database.")


class Action(BaseModel):
    action: Literal["claim", "done", "release", "undo"]
    revision: int = Field(ge=1)
    item_key: str | None = Field(default=None, min_length=64, max_length=64)


@router.post("/dashboard/stores/{store_id}/kitchen/{ticket_id}")
def act(request: Request, store_id: int, ticket_id: int, body: Action):
    csrf = request.session.get("kitchen_csrf")
    supplied = request.headers.get("x-kitchen-csrf", "")
    if not csrf or not secrets.compare_digest(csrf, supplied):
        raise HTTPException(403, "Reload the store display and try again.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Invalid request origin.")
    try:
        with SessionLocal() as session:
            store, connection = scope(request, session, store_id)
            ticket = session.scalar(select(KitchenTicket).where(*filters(store, connection), KitchenTicket.id == ticket_id))
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
            revision = transition(session, ticket, body.action, body.item_key, body.revision, request.session["user_id"])
            session.commit()
        return JSONResponse({"revision": revision}, headers={"Cache-Control": "no-store"})
    except SQLAlchemyError:
        raise HTTPException(503, "Kitchen action could not be saved. Refresh before trying again.")


def stamp(request, store_id):
    # Durable database state lets every Uvicorn process observe committed changes.
    # No connection is held while waiting on the network or sleeping.
    with SessionLocal() as session:
        store, connection = scope(request, session, store_id)
        return tuple(session.execute(select(func.count(KitchenTicket.id), func.coalesce(func.sum(KitchenTicket.revision), 0),
            func.coalesce(func.max(KitchenTicket.id), 0), func.max(KitchenTicket.updated_at).cast(String)).where(*filters(store, connection))).one())


@router.get("/dashboard/stores/{store_id}/kitchen/events")
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
