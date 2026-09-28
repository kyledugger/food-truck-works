"""Organization-scoped setup for stores discovered from Poynt."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from database import SessionLocal
from models import OrganizationStore
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization
from poynt.client import PoyntClient, PoyntReauthorizationRequired
from poynt.connection import get_poynt_credentials

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
        rows = [{
            "id": store_id,
            "poynt_name": name,
            "display_name": configured[store_id].display_name if store_id in configured else "",
            "timezone_name": configured[store_id].timezone_name if store_id in configured else "",
            "is_active": configured[store_id].is_active if store_id in configured else True,
        } for store_id, name in discovered.items()]
    return templates.TemplateResponse(request=request, name="stores.html", context={"stores": rows})


@router.post("/settings/stores")
async def save_store_settings(
    request: Request,
    store_id: str = Form(...),
    display_name: str = Form(""),
    timezone_name: str = Form(...),
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
        row.is_active = is_active
        session.commit()
    return RedirectResponse("/settings/stores", status_code=303)
