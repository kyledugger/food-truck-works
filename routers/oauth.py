from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode


from poynt.token import exchange_authorization_code


from dotenv import load_dotenv
import os
import secrets
from database import SessionLocal
from models import OrganizationMember

from poynt.connection import save_poynt_connection
from permissions import get_organization_role, role_can_manage_integrations
from organization_context import (
    get_current_organization_id,
    user_belongs_to_organization,
)


dotenv_file = os.getenv("DOTENV_FILE", ".env")
load_dotenv(dotenv_file)


from logging_config import configure_logging

import logging
logger = logging.getLogger(__name__)
configure_logging()


POYNT_REDIRECT_URI = os.environ["POYNT_REDIRECT_URI"]
POYNT_APP_ID = os.environ["POYNT_APP_ID"]
POYNT_AUTHORIZE_URL = os.environ["POYNT_AUTHORIZE_URL"]


router = APIRouter()

templates = Jinja2Templates(directory="templates")


@router.get("/oauth/start")
async def oauth_start(request: Request):

    user_id = request.session.get("user_id")

    if not user_id:
        logger.warning(
            "OAUTH SESSION DEBUG [START NO USER]: session_keys=%s",
            sorted(request.session.keys()),
        )
        return RedirectResponse(
            "/login",
            status_code=303
        )

    organization_id = get_current_organization_id(request)
    logger.info(
        "Poynt orders context: user_id=%s, organization_id=%s",
        user_id,
        organization_id,
    )    

    if organization_id is None:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Organization Error",
                "paragraphs": [
                    "No organization is associated with this account."
                ],
                "show_dashboard_link": False,
            },
            status_code=403,
        )

    role = get_organization_role(user_id, organization_id)
    if not role_can_manage_integrations(role):
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Access Denied",
                "paragraphs": [
                    "Only organization owners and managers can connect or reconnect Poynt."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    # Generate a random value that will come back from Poynt
    context = secrets.token_urlsafe(32)

    # Bind this OAuth request to the current organization.
    request.session["poynt_oauth_context"] = context
    request.session["poynt_oauth_organization_id"] = organization_id

    params = {
        "client_id": POYNT_APP_ID,
        "redirect_uri": POYNT_REDIRECT_URI,
        "context": context,
    }

    authorization_url = (
        f"{POYNT_AUTHORIZE_URL}?{urlencode(params)}"
    )

    return RedirectResponse(
        authorization_url,
        status_code=303
    )


@router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback(
    request: Request,
    code: str | None = None,
    status: str | None = None,
    context: str | None = None,
    businessId: str | None = None,
):

    user_id = request.session.get("user_id")

    if not user_id:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "OAuth Error",
                "paragraphs": [
                    "Your Codelian login session could not be found."
                ],
                "show_dashboard_link": False,
            },
            status_code=401,
        )

    expected_context = request.session.get(
        "poynt_oauth_context"
    )
    organization_id = request.session.get("poynt_oauth_organization_id")

    if organization_id is not None:
        try:
            organization_id = int(organization_id)
        except (TypeError, ValueError):
            organization_id = None

    if organization_id is None or not user_belongs_to_organization(
        user_id,
        organization_id,
    ):
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "OAuth Error",
                "paragraphs": [
                    "The organization associated with this authorization request could not be verified."
                ],
                "show_dashboard_link": False,
            },
            status_code=403,
        )

    role = get_organization_role(user_id, organization_id)
    if not role_can_manage_integrations(role):
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Access Denied",
                "paragraphs": [
                    "Your account is no longer authorized to manage the organization's Poynt connection."
                ],
                "show_dashboard_link": True,
            },
            status_code=403,
        )

    if not expected_context:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "OAuth Error",
                "paragraphs": [
                    "No OAuth session was found."
                ],
                "show_dashboard_link": False,
            },
            status_code=400,
        )

    if not context or not secrets.compare_digest(
        context,
        expected_context
    ):
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "OAuth Error",
                "paragraphs": [
                    "OAuth context validation failed."
                ],
                "show_dashboard_link": False,
            },
            status_code=400,
        )

    if not status or status.lower() != "success":
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Authorization",
                "paragraphs": [
                    "Authorization was not completed.",
                    f"Status: {status or 'unknown'}",
                ],
                "show_dashboard_link": False,
            },
            status_code=400,
        )

    if not code:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "OAuth Error",
                "paragraphs": [
                    "Poynt did not provide an authorization code."
                ],
                "show_dashboard_link": False,
            },
            status_code=400,
        )

    # OAuth response is valid.
    # Consume the context so it cannot be reused.
    request.session.pop("poynt_oauth_context", None)
    request.session.pop("poynt_oauth_organization_id", None)

    try:
        token_response = await exchange_authorization_code(
            code=code,
            redirect_uri=POYNT_REDIRECT_URI,
        )

        access_token = token_response["accessToken"]

    except Exception as e:
        logger.error(
            "Poynt catalog request failed: %s",
            e,
        )

        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Token Error",
                "paragraphs": [
                    "Poynt authorization succeeded, but the merchant token request failed.",
                    "Check the Render/application logs.",
                ],
                "show_dashboard_link": False,
            },
            status_code=502,
        )

    if not businessId:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Poynt Error",
                "paragraphs": [
                    "No business ID was returned."
                ],
                "show_dashboard_link": False,
            },
            status_code=400,
        )

    expires_in = token_response.get("expiresIn")

    expires_at = None

    if expires_in:
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=int(expires_in))
        )

    save_poynt_connection(
        organization_id=organization_id,
        business_id=businessId,
        access_token=access_token,
        refresh_token=token_response.get("refreshToken"),
        token_type=token_response.get("tokenType"),
        expires_at=expires_at,
    )

    return RedirectResponse(
        "/dashboard",
        status_code=303
    )
