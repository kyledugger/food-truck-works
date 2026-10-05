"""Database-backed queue worker, shared across Uvicorn processes by leases."""
import asyncio
import logging
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_, delete
from sqlalchemy.exc import IntegrityError
from database import SessionLocal
from models import OrganizationStore, PoyntConnection
from live_dashboard_models import DashboardOrder, DashboardSync, DashboardNotification, DashboardDay
from live_dashboard_metrics import instant, order_store
from poynt.client import PoyntClient, PoyntReauthorizationRequired
from poynt.connection import get_poynt_credentials
from store_time import utc_now, utc_iso, local_day_bounds

logger = logging.getLogger(__name__)


def ensure_sync(session, organization_id, business_id):
    row = session.get(DashboardSync, organization_id)
    if row is None:
        try:
            with session.begin_nested():
                row = DashboardSync(organization_id=organization_id, business_id=business_id)
                session.add(row)
                session.flush()
        except IntegrityError:
            row = session.get(DashboardSync, organization_id)
    if row.business_id != business_id:
        row.business_id = business_id
        row.last_reconciled_at = None
        row.last_webhook_at = None
        row.next_reconcile_at = utc_now()
        row.hook_id = None
        row.error = None
        row.lease_token = None
        row.lease_until = None
    return row


def acquire(organization_id):
    now = utc_now()
    with SessionLocal() as session:
        state = session.scalar(select(DashboardSync).where(
            DashboardSync.organization_id == organization_id,
            or_(DashboardSync.lease_until.is_(None), DashboardSync.lease_until <= now)
        ).with_for_update(skip_locked=True))
        if state is None:
            return None
        token = uuid.uuid4().hex
        state.lease_token = token
        state.lease_until = now + timedelta(seconds=180)
        business_id = state.business_id
        session.commit()
        return business_id, token


def owned_state(session, organization_id, business_id, token):
    return session.scalar(select(DashboardSync).where(
        DashboardSync.organization_id == organization_id,
        DashboardSync.business_id == business_id,
        DashboardSync.lease_token == token,
        DashboardSync.lease_until > utc_now(),
    ).with_for_update())


def release(organization_id, token):
    with SessionLocal() as session:
        state = session.scalar(select(DashboardSync).where(
            DashboardSync.organization_id == organization_id,
            DashboardSync.lease_token == token).with_for_update())
        if state:
            state.lease_until = None
            state.lease_token = None
            session.commit()


def save_order(session, organization_id, business_id, order):
    at = instant(order.get("createdAt"))
    updated = instant(order.get("updatedAt") or order.get("createdAt"))
    order_id = str(order["id"])
    if len(order_id) > 100:
        raise ValueError("Invalid order ID")
    row = session.scalar(select(DashboardOrder).where(
        DashboardOrder.organization_id == organization_id,
        DashboardOrder.business_id == business_id, DashboardOrder.order_id == order_id))
    if row and row.provider_updated_at > updated:
        return
    if row is None:
        row = DashboardOrder(organization_id=organization_id, business_id=business_id, order_id=order_id)
        session.add(row)
    # Persist reporting fields only, excluding customer/contact/payment details.
    payload = {key: order.get(key) for key in ("id", "createdAt", "updatedAt", "amounts", "statuses")}
    payload["items"] = [{key: item.get(key) for key in
        ("sku", "quantity", "unitPrice", "discount", "status")} for item in order.get("items") or []]
    row.payload = payload
    row.store_id = order_store(order)
    row.created_at = at
    row.provider_updated_at = updated
    session.flush()


async def process_organization(organization_id, business_id, token):
    credentials = get_poynt_credentials(organization_id)
    if not credentials or credentials.business_id != business_id:
        return
    client = PoyntClient(credentials, organization_id)
    now = utc_now()
    with SessionLocal() as session:
        state = session.get(DashboardSync, organization_id)
        reconcile = state.next_reconcile_at <= now
        stores = session.scalars(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id, OrganizationStore.is_active.is_(True))).all()
        valid = []
        for store in stores:
            try:
                zone = ZoneInfo(store.timezone_name or "")
                start, end = local_day_bounds(now.astimezone(zone).date(), zone)
                valid.append((start, end))
            except (ValueError, KeyError):
                pass
        pending = [(n.id, n.order_id) for n in session.scalars(select(DashboardNotification).where(
            DashboardNotification.organization_id == organization_id,
            DashboardNotification.business_id == business_id,
            DashboardNotification.processed_at.is_(None), DashboardNotification.retry_at <= now
        ).order_by(DashboardNotification.id).limit(20)).all()]
        historical_days = [(row.id, row.report_date) for row in session.scalars(select(DashboardDay).where(
            DashboardDay.organization_id == organization_id, DashboardDay.business_id == business_id,
            DashboardDay.next_load_at <= now, DashboardDay.requested_at >= now - timedelta(minutes=5)
        ).order_by(DashboardDay.next_load_at).limit(1)).all()]
    # Notifications first; reconciliation may be a larger initial fetch.
    for notification_id, order_id in pending:
        try:
            order = await client.get_order(order_id)
            if str(order.get("id")) != order_id:
                raise ValueError("Unexpected order ID")
            with SessionLocal() as session:
                if not owned_state(session, organization_id, business_id, token):
                    return
                save_order(session, organization_id, business_id, order)
                notification = session.get(DashboardNotification, notification_id)
                notification.processed_at = utc_now()
                session.commit()
        except Exception:
            logger.warning("Dashboard notification retry scheduled: organization=%s notification=%s", organization_id, notification_id)
            with SessionLocal() as session:
                if not owned_state(session, organization_id, business_id, token):
                    return
                notification = session.get(DashboardNotification, notification_id)
                notification.retry_at = utc_now() + timedelta(seconds=60)
                session.commit()
    for day_id, report_date in historical_days:
        try:
            bounds = []
            for store in stores:
                try:
                    bounds.append(local_day_bounds(report_date, ZoneInfo(store.timezone_name or "")))
                except (ValueError, KeyError):
                    pass
            if not bounds:
                raise ValueError("No configured store timezone")
            orders = await client.get_recent_orders(start_at=utc_iso(min(b[0] for b in bounds)),
                end_at=utc_iso(max(b[1] for b in bounds)), fetch_all=True)
            with SessionLocal() as session:
                if not owned_state(session, organization_id, business_id, token):
                    return
                for order in orders:
                    save_order(session, organization_id, business_id, order)
                day = session.get(DashboardDay, day_id)
                day.loaded_at = utc_now()
                day.next_load_at = utc_now() + timedelta(minutes=5)
                day.error = None
                session.commit()
        except Exception:
            logger.warning("Historical dashboard retry scheduled: organization=%s day=%s", organization_id, report_date)
            with SessionLocal() as session:
                if not owned_state(session, organization_id, business_id, token):
                    return
                day = session.get(DashboardDay, day_id)
                day.error = "Historical sales could not load. Retrying shortly."
                day.next_load_at = utc_now() + timedelta(seconds=60)
                session.commit()
    if reconcile and valid:
        # Include the previous comparison hour across local midnight.
        start = min(v[0] for v in valid) - timedelta(minutes=65)
        end = max(v[1] for v in valid)
        orders = await client.get_recent_orders(start_at=utc_iso(start), end_at=utc_iso(end), fetch_all=True)
        with SessionLocal() as session:
            state = owned_state(session, organization_id, business_id, token)
            if not state:
                return
            for order in orders:
                save_order(session, organization_id, business_id, order)
            state.last_reconciled_at = utc_now()
            state.next_reconcile_at = utc_now() + timedelta(minutes=5)
            state.error = None
            cutoff = utc_now() - timedelta(days=90)
            session.execute(delete(DashboardOrder).where(DashboardOrder.organization_id == organization_id,
                DashboardOrder.created_at < cutoff))
            session.execute(delete(DashboardNotification).where(DashboardNotification.organization_id == organization_id,
                DashboardNotification.processed_at < utc_now() - timedelta(days=3)))
            session.execute(delete(DashboardDay).where(DashboardDay.organization_id == organization_id,
                DashboardDay.requested_at < cutoff))
            session.commit()
    elif reconcile:
        with SessionLocal() as session:
            state = owned_state(session, organization_id, business_id, token)
            if state:
                state.next_reconcile_at = utc_now() + timedelta(minutes=5)
                session.commit()


async def dashboard_worker():
    while True:
        try:
            with SessionLocal() as session:
                now = utc_now()
                pending_orgs = select(DashboardNotification.organization_id).where(
                    DashboardNotification.processed_at.is_(None), DashboardNotification.retry_at <= now)
                historical_orgs = select(DashboardDay.organization_id).where(
                    DashboardDay.next_load_at <= now, DashboardDay.requested_at >= now - timedelta(minutes=5))
                ids = session.scalars(select(DashboardSync.organization_id).join(PoyntConnection,
                    PoyntConnection.organization_id == DashboardSync.organization_id).where(
                    PoyntConnection.business_id == DashboardSync.business_id,
                    or_(DashboardSync.next_reconcile_at <= now, DashboardSync.organization_id.in_(pending_orgs),
                        DashboardSync.organization_id.in_(historical_orgs))
                ).order_by(DashboardSync.next_reconcile_at).limit(50)).all()
            for organization_id in ids:
                claim = acquire(organization_id)
                if claim is None:
                    continue
                business_id, token = claim
                try:
                    await asyncio.wait_for(process_organization(organization_id, business_id, token), timeout=150)
                except Exception as exc:
                    logger.warning("Dashboard sync failed: organization=%s error_type=%s", organization_id, type(exc).__name__)
                    with SessionLocal() as session:
                        state = owned_state(session, organization_id, business_id, token)
                        if state:
                            state.error = "Reconnect Poynt to resume updates." if isinstance(exc, PoyntReauthorizationRequired) else "Order sync failed. Retrying shortly."
                            state.next_reconcile_at = utc_now() + timedelta(seconds=60)
                            session.commit()
                finally:
                    release(organization_id, token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A missing migration must not prevent the rest of the app starting.
            logger.warning("Dashboard worker unavailable: %s", type(exc).__name__)
        await asyncio.sleep(5)
