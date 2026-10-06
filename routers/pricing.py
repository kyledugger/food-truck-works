"""Manager product discovery. Poynt access is read-only."""
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from database import SessionLocal
from models import Organization, OrganizationStore, PricingProduct, ProductDiscovery
from organization_context import get_current_organization_id
from permissions import get_organization_role, role_can_manage_organization
from poynt.client import PoyntClient, PoyntAPIError, PoyntReauthorizationRequired
from poynt.connection import get_poynt_credentials
from product_discovery import discover_products, compare_products

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)


def manager_org(request):
    user = request.session.get("user_id")
    org = get_current_organization_id(request) if user else None
    if not org or not role_can_manage_organization(get_organization_role(user, org)):
        raise HTTPException(403, "Owner or manager permission is required.")
    return org


def check_csrf(request, token):
    expected = request.session.get("pricing_csrf")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(403, "Reload Products and try again.")


def require_store(session, org, store_id):
    store = session.scalar(select(OrganizationStore).where(
        OrganizationStore.organization_id == org,
        OrganizationStore.store_id == store_id, OrganizationStore.is_active.is_(True)))
    if not store:
        raise HTTPException(404, "Choose an active store belonging to this organization.")
    return store


def snapshot_for(session, org, store_id):
    return session.scalar(select(ProductDiscovery).where(
        ProductDiscovery.organization_id == org, ProductDiscovery.store_id == store_id))


@router.get("/pricing")
def pricing_home(request: Request):
    manager_org(request)
    return RedirectResponse("/pricing/products", status_code=303)


@router.get("/pricing/products")
def products_page(request: Request, store_id: str = "", notice: str = ""):
    org = manager_org(request)
    csrf = request.session.setdefault("pricing_csrf", secrets.token_urlsafe(32))
    with SessionLocal() as session:
        stores = session.scalars(select(OrganizationStore).where(
            OrganizationStore.organization_id == org, OrganizationStore.is_active.is_(True)
        ).order_by(OrganizationStore.poynt_name)).all()
        if not store_id and len(stores) == 1:
            store_id = stores[0].store_id
        store = require_store(session, org, store_id) if store_id else None
        definitions = session.scalars(select(PricingProduct).where(
            PricingProduct.organization_id == org).order_by(PricingProduct.sku)).all()
        snapshot = snapshot_for(session, org, store_id) if store else None
        credentials = get_poynt_credentials(org)
        stale = bool(snapshot and (not credentials or snapshot.business_id != credentials.business_id))
        rows, missing = compare_products(snapshot.payload["products"], definitions) if snapshot and not stale else ([], [])
        return templates.TemplateResponse(request=request, name="pricing_products.html", context={
            "stores": stores, "selected_store": store, "definitions": definitions,
            "snapshot": snapshot if not stale else None, "stale": stale,
            "rows": rows, "missing": missing, "csrf": csrf,
            "notice": {"discovered": "Products refreshed from Poynt.",
                       "added": "Selected SKUs added to the authoritative list.",
                       "removed": "SKU removed from the FTW list. Poynt was not changed."}.get(notice, ""),
            "error": "", "can_connect": credentials is not None,
        }, headers={"Cache-Control": "no-store"})


@router.post("/pricing/products/discover")
async def discover(request: Request, store_id: str = Form(...), csrf: str = Form(...)):
    org = manager_org(request)
    check_csrf(request, csrf)
    with SessionLocal() as session:
        require_store(session, org, store_id)
    credentials = get_poynt_credentials(org)
    if not credentials:
        raise HTTPException(409, "Connect Poynt before discovering products.")
    try:
        products = await discover_products(PoyntClient(credentials, org), store_id)
    except PoyntReauthorizationRequired as exc:
        response = products_page(request, store_id)
        return templates.TemplateResponse(request=request, name="pricing_products.html",
            context=dict(response.context, error="Reconnect Poynt from Integrations, then refresh products."),
            status_code=409, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        # Preserve the previous complete snapshot after any failed discovery.
        logger.warning("Product discovery failed for organization_id=%s; type=%s", org, type(exc).__name__)
        if isinstance(exc, PoyntAPIError):
            # API errors here contain our controlled diagnostic text, never
            # provider response bodies or credentials.
            logger.warning("Product discovery diagnostic: %s", str(exc))
        response = products_page(request, store_id)
        return templates.TemplateResponse(request=request, name="pricing_products.html",
            context=dict(response.context, error="Could not complete product discovery. Previous results were kept. Try again."),
            status_code=502, headers={"Cache-Control": "no-store"})
    with SessionLocal() as session:
        session.scalar(select(Organization.id).where(Organization.id == org).with_for_update())
        require_store(session, org, store_id)
        snapshot = snapshot_for(session, org, store_id)
        if snapshot is None:
            snapshot = ProductDiscovery(organization_id=org, store_id=store_id)
            session.add(snapshot)
        snapshot.business_id = credentials.business_id
        snapshot.token = secrets.token_urlsafe(24)
        snapshot.payload = {"discovered_at": datetime.now(timezone.utc).isoformat(), "products": products}
        session.commit()
    return RedirectResponse("/pricing/products?" + urlencode({"store_id": store_id, "notice": "discovered"}), 303)


@router.post("/pricing/products/add")
def add_definitions(request: Request, store_id: str = Form(...), csrf: str = Form(...),
                    snapshot_token: str = Form(...), product_ids: list[str] = Form([])):
    org = manager_org(request)
    check_csrf(request, csrf)
    credentials = get_poynt_credentials(org)
    with SessionLocal() as session:
        session.scalar(select(Organization.id).where(Organization.id == org).with_for_update())
        require_store(session, org, store_id)
        snapshot = snapshot_for(session, org, store_id)
        if (not snapshot or not secrets.compare_digest(snapshot.token, snapshot_token)
                or not credentials or snapshot.business_id != credentials.business_id):
            raise HTTPException(409, "Discovery changed. Refresh Products before adding SKUs.")
        definitions = session.scalars(select(PricingProduct).where(PricingProduct.organization_id == org)).all()
        rows, _ = compare_products(snapshot.payload["products"], definitions)
        by_id = {p["id"]: p for p in rows}
        selected = set(product_ids)
        if not selected or any(pid not in by_id or not by_id[pid]["can_add"] for pid in selected):
            raise HTTPException(400, "Select unique, valid, new SKUs from the current discovery.")
        for pid in selected:
            row = by_id[pid]
            session.add(PricingProduct(organization_id=org, sku=row["sku"],
                name=row["name"][:200], category=row["category"][:200] or None))
        session.commit()
    return RedirectResponse("/pricing/products?" + urlencode({"store_id": store_id, "notice": "added"}), 303)


@router.post("/pricing/products/remove")
def remove_definition(request: Request, definition_id: int = Form(...), csrf: str = Form(...), store_id: str = Form("")):
    org = manager_org(request)
    check_csrf(request, csrf)
    with SessionLocal() as session:
        session.scalar(select(Organization.id).where(Organization.id == org).with_for_update())
        if store_id:
            require_store(session, org, store_id)
        definition = session.scalar(select(PricingProduct).where(
            PricingProduct.id == definition_id, PricingProduct.organization_id == org))
        if not definition:
            raise HTTPException(404, "SKU definition not found.")
        session.delete(definition)
        session.commit()
    return RedirectResponse("/pricing/products?" + urlencode({"store_id": store_id, "notice": "removed"}), 303)
