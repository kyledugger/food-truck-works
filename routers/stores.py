"""Organization-scoped setup for stores discovered from Poynt."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from booking_resources import ensure_intrinsic_resource, store_booking_name
from database import SessionLocal
from models import BookingResource, OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization
from poynt.client import PoyntClient, PoyntReauthorizationRequired
from poynt.connection import get_poynt_credentials
from store_types import INTRINSIC_BOOKABLE_TYPES, STORE_TYPES

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _manager_org(request: Request) -> int:
    user_id = request.session.get("user_id")
    organization_id = get_current_organization_id(request) if user_id else None
    if organization_id is None or not role_can_manage_organization(
        get_organization_role(user_id, organization_id)
    ):
        raise HTTPException(403, "Manager permission is required.")
    return organization_id


def _poynt_store_rows(payload):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("stores", payload.get("content"))
    else:
        rows = None
    if not isinstance(rows, list):
        raise HTTPException(502, "Poynt returned an unexpected store list.")
    stores = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        store_id = str(row["id"]).lower()
        stores[store_id] = str(row.get("name") or row.get("displayName") or store_id)[:200]
    return stores


async def _discover(organization_id):
    credentials = get_poynt_credentials(organization_id)
    if not credentials:
        raise HTTPException(409, "Connect Poynt before setting up stores.")
    try:
        return _poynt_store_rows(await PoyntClient(credentials, organization_id=organization_id).get_stores())
    except PoyntReauthorizationRequired as exc:
        raise HTTPException(401, "Reconnect Poynt to refresh stores.") from exc


@router.get("/settings/stores", response_class=HTMLResponse)
async def store_settings(request: Request):
    organization_id = _manager_org(request)
    discovered = await _discover(organization_id)
    with SessionLocal() as session:
        configured = {row.store_id: row for row in session.execute(
            select(OrganizationStore).where(OrganizationStore.organization_id == organization_id)
        ).scalars()}
        resources = {row.organization_store_id: row for row in session.execute(
            select(BookingResource).join(OrganizationStore).where(
                OrganizationStore.organization_id == organization_id
            )
        ).scalars()}
        rows = [{
            "id": store_id,
            "poynt_name": name,
            "display_name": configured[store_id].display_name if store_id in configured else "",
            "timezone_name": configured[store_id].timezone_name if store_id in configured else "",
            "store_type": configured[store_id].store_type if store_id in configured else None,
            "is_active": configured[store_id].is_active if store_id in configured else True,
            "resource": resources.get(configured[store_id].id) if store_id in configured else None,
        } for store_id, name in discovered.items()]
    return templates.TemplateResponse(request=request, name="stores.html", context={
        "stores": rows, "store_types": STORE_TYPES,
        "intrinsic_types": INTRINSIC_BOOKABLE_TYPES,
    })


@router.post("/settings/stores")
async def save_store_settings(
    request: Request,
    store_id: str = Form(...),
    display_name: str = Form(""),
    timezone_name: str = Form(...),
    store_type: str = Form(""),
    is_active: bool = Form(False),
):
    organization_id = _manager_org(request)
    store_id = store_id.lower().strip()
    discovered = await _discover(organization_id)
    if not store_id or len(store_id) > 100 or store_id not in discovered:
        raise HTTPException(400, "Store is not part of this Poynt connection.")
    display_name = display_name.strip()
    if len(display_name) > 200:
        raise HTTPException(400, "Store name is too long.")
    if store_type not in STORE_TYPES:
        raise HTTPException(400, "Choose a store type.")
    if not timezone_name or len(timezone_name) > 100:
        raise HTTPException(400, "Choose a valid store timezone.")
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(400, "Choose a valid IANA timezone, such as America/Phoenix.") from exc
    with SessionLocal() as session:
        row = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.store_id == store_id,
        )).scalar_one_or_none()
        if row is None:
            row = OrganizationStore(organization_id=organization_id, store_id=store_id, poynt_name=discovered[store_id])
            session.add(row)
        row.poynt_name = discovered[store_id]
        row.display_name = display_name or None
        row.timezone_name = timezone_name
        row.store_type = store_type
        row.is_active = is_active
        ensure_intrinsic_resource(row)
        session.commit()
    return RedirectResponse("/settings/stores", status_code=303)


@router.post("/settings/stores/booking")
async def save_store_booking(
    request: Request,
    store_id: str = Form(...),
    is_enabled: bool = Form(False),
    resource_name: str = Form(""),
    capacity: int = Form(1),
):
    organization_id = _manager_org(request)
    store_id = store_id.lower().strip()
    if not store_id or len(store_id) > 100:
        raise HTTPException(400, "Invalid store ID.")
    with SessionLocal() as session:
        store = session.execute(select(OrganizationStore).where(
            OrganizationStore.organization_id == organization_id,
            OrganizationStore.store_id == store_id,
        )).scalar_one_or_none()
        if store is None or store.store_type not in STORE_TYPES:
            raise HTTPException(404, "Configure this store before setting up bookings.")
        if store.store_type in INTRINSIC_BOOKABLE_TYPES:
            raise HTTPException(400, "Mobile stores already have an automatic booking resource.")
        if capacity < 1 or capacity > 100 or (store.store_type != "catering" and capacity != 1):
            raise HTTPException(400, "Choose a valid concurrent booking capacity.")
        resource_name = resource_name.strip()
        if is_enabled and (not resource_name or len(resource_name) > 200):
            raise HTTPException(400, "Enter a booking resource name (up to 200 characters).")
        resource = store.booking_resource
        if resource is None:
            if not is_enabled:
                return RedirectResponse("/settings/stores", status_code=303)
            resource = BookingResource(name=resource_name, name_follows_store=False)
            store.booking_resource = resource
        if is_enabled:
            resource.name = resource_name
            resource.name_follows_store = resource_name == store_booking_name(store)
            resource.capacity = capacity
        resource.is_enabled = is_enabled
        session.commit()
    return RedirectResponse("/settings/stores", status_code=303)
