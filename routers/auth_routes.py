from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from fastapi.templating import Jinja2Templates
import secrets
import time

from auth import (
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_password,
)
from database import SessionLocal
from models import User, Organization, OrganizationMember
from email_service import EmailDeliveryError, send_verification_email
from security_logging import log_security_event
from store_display_access import display_assignment
from security_tokens import (
    EMAIL_VERIFICATION,
    EMAIL_VERIFICATION_LIFETIME,
    create_security_token,
)

import os
from dotenv import load_dotenv

dotenv_file = os.getenv("DOTENV_FILE", ".env")
load_dotenv(dotenv_file)

from logging_config import configure_logging
import logging

logger = logging.getLogger(__name__)
configure_logging()

router = APIRouter()
templates = Jinja2Templates(directory="templates")

ORGANIZATION_REGISTRATION_SESSION_KEY = "organization_registration_authorized_at"
ORGANIZATION_REGISTRATION_ACCESS_SECONDS = 30 * 60


def organization_registration_is_authorized(
    request: Request,
    *,
    now: float | None = None,
    environment: str | None = None,
) -> bool:
    environment = environment or os.getenv("ENVIRONMENT", "local")
    if environment != "production":
        return True

    authorized_at = request.session.get(ORGANIZATION_REGISTRATION_SESSION_KEY)
    if not isinstance(authorized_at, (int, float)):
        return False

    age = (time.time() if now is None else now) - authorized_at
    return 0 <= age <= ORGANIZATION_REGISTRATION_ACCESS_SECONDS


@router.get("/organization-registration-access", response_class=HTMLResponse)
async def organization_registration_access_page(request: Request):
    if organization_registration_is_authorized(request):
        return RedirectResponse("/register", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="registration_access.html",
    )


@router.post("/organization-registration-access", response_class=HTMLResponse)
async def organization_registration_access(
    request: Request,
    access_code: str = Form(...),
):
    configured_code = os.getenv("ORG_REGISTRATION_ACCESS_CODE", "")
    access_code = access_code.strip()
    if len(configured_code) < 32:
        logger.error(
            "Organization registration access is unavailable because "
            "ORG_REGISTRATION_ACCESS_CODE is missing or too short"
        )
        log_security_event(
            request,
            "organization_registration_access",
            "failed",
            level=logging.ERROR,
            reason="access_code_not_configured",
        )
        return templates.TemplateResponse(
            request=request,
            name="registration_access.html",
            context={"error": "Registration access is not currently available."},
            status_code=503,
        )

    if len(access_code) > 256 or not secrets.compare_digest(
        access_code, configured_code
    ):
        log_security_event(
            request,
            "organization_registration_access",
            "denied",
            level=logging.WARNING,
            reason="invalid_access_code",
        )
        return templates.TemplateResponse(
            request=request,
            name="registration_access.html",
            context={"error": "The access code is not valid."},
            status_code=401,
        )

    request.session[ORGANIZATION_REGISTRATION_SESSION_KEY] = int(time.time())
    log_security_event(
        request,
        "organization_registration_access",
        "succeeded",
    )
    return RedirectResponse("/register", status_code=303)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if not organization_registration_is_authorized(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="register.html"
    )


@router.post("/register")
async def register(
    request: Request,
    organization_name: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    if not organization_registration_is_authorized(request):
        log_security_event(
            request,
            "registration",
            "denied",
            level=logging.WARNING,
            reason="registration_not_authorized",
        )
        return RedirectResponse("/", status_code=303)

    organization_name = organization_name.strip()
    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()
    phone = phone.strip() or None

    form_values = {
        "organization_name": organization_name,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone or "",
    }

    if not organization_name:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "Organization name is required.",
                **form_values,
            },
            status_code=400
        )

    if not first_name or not last_name:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": "First and last name are required.", **form_values},
            status_code=400,
        )

    if len(first_name) > 100 or len(last_name) > 100:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "First and last name must be 100 characters or fewer.",
                **form_values,
            },
            status_code=400,
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": "Passwords do not match.",
                **form_values,
            },
            status_code=400
        )

    password_error = validate_password(password)
    if password_error:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "error": password_error,
                **form_values,
            },
            status_code=400
        )

    with SessionLocal() as session:
        existing_user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        if existing_user:
            log_security_event(
                request,
                "registration",
                "denied",
                level=logging.WARNING,
                user_id=existing_user.id,
                reason="account_already_exists",
            )
            return templates.TemplateResponse(
                request=request,
                name="register.html",
                context={
                    "error": (
                        "We could not create an account with those details. "
                        "If you may already have an account, try signing in."
                    ),
                    **form_values,
                },
                status_code=400
            )

        # User, organization, and owner membership are created
        # in the same transaction.
        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            password_hash=hash_password(password)
        )
        session.add(user)
        session.flush()

        organization = Organization(
            name=organization_name
        )
        session.add(organization)
        session.flush()

        membership = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            role="owner"
        )
        session.add(membership)

        raw_token = create_security_token(
            session,
            user.id,
            EMAIL_VERIFICATION,
            EMAIL_VERIFICATION_LIFETIME,
        )
        try:
            send_verification_email(user.email, raw_token)
            session.commit()
        except EmailDeliveryError:
            session.rollback()
            logger.exception("Registration verification email delivery failed")
            log_security_event(
                request,
                "registration",
                "failed",
                level=logging.ERROR,
                reason="email_delivery_failed",
            )
            return templates.TemplateResponse(
                request=request,
                name="register.html",
                context={
                    "error": (
                        "We could not send a verification email. "
                        "Please try again shortly."
                    ),
                    **form_values,
                },
                status_code=503,
            )

        log_security_event(
            request,
            "registration",
            "succeeded",
            user_id=user.id,
            organization_id=organization.id,
        )

        request.session.clear()

    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Check Your Email",
            "paragraphs": [
                "We sent a verification link to your email address. "
                "Verify your email before signing in. The link expires in 24 hours."
            ],
            "login_link": True,
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    message = None
    if request.query_params.get("verified") == "1":
        message = "Your email is verified. You can now sign in."
    elif request.query_params.get("password_reset") == "1":
        message = "Your password was reset. You can now sign in."
    elif request.query_params.get("email_changed") == "1":
        message = "Your email was changed. Sign in with your new email address."
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"message": message},
    )


@router.get("/login/store-display", response_class=HTMLResponse)
def display_login_page(request: Request):
    return templates.TemplateResponse(request=request, name="display_login.html", headers={"Cache-Control": "no-store"})


@router.post("/login/store-display")
def display_login(request: Request, organization_code: str = Form(...), username: str = Form(...), password: str = Form(...)):
    code, username = organization_code.strip().lower(), username.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(User).join(OrganizationMember, OrganizationMember.user_id == User.id)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(Organization.display_login_code == code, OrganizationMember.display_username == username,
                   User.account_type == "store_display"))
        valid = verify_password(password, user.password_hash if user else None)
        display = display_assignment(session, user.id) if user and valid else None
        if not display:
            log_security_event(request, "store_display_login", "failed", user_id=user.id if user else None)
            return templates.TemplateResponse(request=request, name="display_login.html",
                context={"error": "Unable to sign in. Check your organization code, username and password, or contact a manager.",
                         "organization_code": code, "username": username}, status_code=401, headers={"Cache-Control": "no-store"})
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            session.commit()
        assignment, store = display
        request.session.clear()
        request.session.update(user_id=user.id, organization_id=store.organization_id,
            store_assignment_id=assignment.id, store_session_version=assignment.session_version)
        log_security_event(request, "store_display_login", "succeeded", user_id=user.id, organization_id=store.organization_id)
        return RedirectResponse(f"/dashboard/stores/{store.id}/home", status_code=303)


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    email = email.strip().lower()

    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()

        password_valid = verify_password(
            password,
            user.password_hash if user else None,
        )

        if not user or not password_valid or not user.is_active or user.account_type != "person":
            if user is None:
                failure_reason = "unknown_account"
            elif not user.is_active:
                failure_reason = "inactive_account"
            else:
                failure_reason = "invalid_password"

            log_security_event(
                request,
                "login",
                "failed",
                level=logging.WARNING,
                user_id=user.id if user else None,
                reason=failure_reason,
            )
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": "Invalid email or password."
                },
                status_code=401
            )

        if user.email_verified_at is None:
            log_security_event(
                request,
                "login",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="email_not_verified",
            )
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": (
                        "Verify your email before signing in. "
                        "You can request a new verification link below."
                    )
                },
                status_code=403,
            )

        memberships = session.execute(
            select(OrganizationMember)
            .where(OrganizationMember.user_id == user.id)
            .order_by(OrganizationMember.id)
        ).scalars().all()

        if not memberships:
            log_security_event(
                request,
                "login",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="no_organization_membership",
            )
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "error": (
                        "Unable to sign in. Please contact support "
                        "if this continues."
                    )
                },
                status_code=403
            )

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            session.commit()
            log_security_event(
                request,
                "password_hash_upgrade",
                "succeeded",
                user_id=user.id,
            )

        request.session.clear()
        request.session["user_id"] = user.id

        if len(memberships) == 1:
            request.session["organization_id"] = memberships[0].organization_id
            destination = "/dashboard"
        else:
            # Do not silently choose an organization for a multi-organization user.
            request.session.pop("organization_id", None)
            destination = "/organizations/select"

        log_security_event(
            request,
            "login",
            "succeeded",
            user_id=user.id,
            organization_id=(
                memberships[0].organization_id
                if len(memberships) == 1
                else None
            ),
            membership_count=len(memberships),
        )

    return RedirectResponse(
        destination,
        status_code=303
    )


@router.get("/organizations/select", response_class=HTMLResponse)
async def select_organization_page(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not user or not user.is_active:
            log_security_event(
                request,
                "session",
                "invalidated",
                level=logging.WARNING,
                user_id=user_id,
                reason="missing_or_inactive_user",
            )
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        memberships = session.execute(
            select(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.id)
        ).scalars().all()

        if not memberships:
            log_security_event(
                request,
                "session",
                "invalidated",
                level=logging.WARNING,
                user_id=user_id,
                reason="no_organization_membership",
            )
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        organizations = []
        for membership in memberships:
            organization = session.get(Organization, membership.organization_id)
            if organization:
                organizations.append({
                    "id": organization.id,
                    "name": organization.name,
                    "role": membership.role,
                })

    return templates.TemplateResponse(
        request=request,
        name="organization_select.html",
        context={
            "user": user,
            "organizations": organizations,
            "active_organization_id": request.session.get("organization_id"),
        },
    )


@router.post("/organizations/select")
async def select_organization(
    request: Request,
    organization_id: int = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if not user or not user.is_active:
            log_security_event(
                request,
                "session",
                "invalidated",
                level=logging.WARNING,
                user_id=user_id,
                reason="missing_or_inactive_user",
            )
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        membership = session.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
            )
        ).scalar_one_or_none()

        if not membership:
            log_security_event(
                request,
                "organization_selection",
                "denied",
                level=logging.WARNING,
                user_id=user_id,
                organization_id=organization_id,
                reason="membership_not_found",
            )
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Organization Access Denied",
                    "message": "You do not have access to that organization.",
                },
                status_code=403,
            )

        request.session["organization_id"] = membership.organization_id
        log_security_event(
            request,
            "organization_selection",
            "succeeded",
            user_id=user_id,
            organization_id=membership.organization_id,
        )

    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    user_id = request.session.get("user_id")
    organization_id = request.session.get("organization_id")
    log_security_event(
        request,
        "logout",
        "succeeded",
        user_id=user_id if isinstance(user_id, int) else None,
        organization_id=(
            organization_id if isinstance(organization_id, int) else None
        ),
    )
    request.session.clear()

    return RedirectResponse(
        "/login",
        status_code=303
    )
