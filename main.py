import os
import asyncio
from contextlib import asynccontextmanager, suppress
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from sqlalchemy import select
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from database import SessionLocal
from models import User, Organization, OrganizationMember
from organization_context import get_current_organization_id
from permissions import (
    get_organization_role,
    role_can_manage_employees,
    role_can_manage_integrations,
    role_can_view_dashboard_sales,
)
from poynt.connection import (
    get_poynt_connection
)
from security_logging import log_security_event

from routers.auth_routes import router as auth_router
from routers.oauth import router as oauth_router
from routers.poynt import router as poynt_router
from routers.employees import router as employees_router
from routers.account_security import router as account_security_router
from routers.account_settings import router as account_settings_router
from routers.stores import router as stores_router
from routers.integrations import router as integrations_router
from routers.live_dashboard import router as live_dashboard_router
from live_dashboard_service import dashboard_worker
from store_display_access import StoreDisplayMiddleware

dotenv_file = os.getenv("DOTENV_FILE", ".env")
load_dotenv(dotenv_file)

from logging_config import configure_logging

import logging
logger = logging.getLogger(__name__)

configure_logging()

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")

if ENVIRONMENT == "local-prod-db":
    logger.warning("============================================================")
    logger.warning("LOCAL APPLICATION")
    logger.warning("DATABASE: PRODUCTION")
    logger.warning("============================================================")
elif ENVIRONMENT == "production":
    logger.info("============================================================")
    logger.info("PRODUCTION APPLICATION")
    logger.info("DATABASE: PRODUCTION")
    logger.info("============================================================")
else:
    logger.info("============================================================")
    logger.info("LOCAL APPLICATION")
    logger.info("DATABASE: LOCAL")
    logger.info("============================================================")

POYNT_APP_ID = os.environ["POYNT_APP_ID"]
POYNT_AUTHORIZE_URL = os.environ["POYNT_AUTHORIZE_URL"]

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(dashboard_worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Food Truck Works", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth_router)
app.include_router(oauth_router)
app.include_router(poynt_router)
app.include_router(employees_router)
app.include_router(account_security_router)
app.include_router(account_settings_router)
app.include_router(stores_router)
app.include_router(integrations_router)
app.include_router(live_dashboard_router)

is_production = os.getenv("ENVIRONMENT") == "production"
# SessionMiddleware is added last so signed sessions exist before this guard.
app.add_middleware(StoreDisplayMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    https_only=is_production,
    same_site="lax",
    max_age=60 * 60 * 24 * 14,
)


templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="coming_soon.html",
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):

    user_id = request.session.get("user_id")

    if not user_id:

        return RedirectResponse(
            "/login",
            status_code=303
        )

    with SessionLocal() as session:

        user = session.get(User, user_id)

        if not user:
            log_security_event(
                request,
                "session",
                "invalidated",
                level=logging.WARNING,
                user_id=user_id,
                reason="user_not_found",
            )
            request.session.clear()

            return RedirectResponse(
                "/login",
                status_code=303
            )

    organization_id = get_current_organization_id(request)

    if organization_id is None:
        return RedirectResponse(
            "/organizations/select",
            status_code=303
        )

    role = get_organization_role(user_id, organization_id)
    if role is None:
        log_security_event(
            request,
            "organization_access",
            "denied",
            level=logging.WARNING,
            user_id=user_id,
            organization_id=organization_id,
            reason="membership_not_found",
        )
        request.session.pop("organization_id", None)
        return RedirectResponse("/organizations/select", status_code=303)

    with SessionLocal() as session:
        active_organization = session.get(Organization, organization_id)
        memberships = session.execute(
            select(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.id)
        ).scalars().all()

        organization_options = []
        for membership in memberships:
            organization = session.get(Organization, membership.organization_id)
            if organization:
                organization_options.append({
                    "id": organization.id,
                    "name": organization.name,
                    "role": membership.role,
                })

    can_manage_poynt = role_can_manage_integrations(role)
    can_manage_employees = role_can_manage_employees(role)
    poynt_connection = get_poynt_connection(organization_id)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "poynt_connection": poynt_connection,
            "organization_role": role,
            "active_organization": active_organization,
            "organization_options": organization_options,
            "can_manage_poynt": can_manage_poynt,
            "can_manage_employees": can_manage_employees,
            "can_view_dashboard_sales": role_can_view_dashboard_sales(role),
        }
    )



@app.get("/health")
async def health():

    return {
        "status": "healthy"
    }
