import logging
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from auth import hash_password, validate_password, verify_password
from database import SessionLocal
from email_service import (
    EmailDeliveryError,
    send_email_change_verification,
    send_email_changed_notice,
    send_password_changed_email,
)
from models import User, UserSecurityToken
from security_logging import log_security_event
from security_tokens import (
    EMAIL_CHANGE,
    EMAIL_CHANGE_LIFETIME,
    PASSWORD_RESET,
    consume_security_token,
    create_security_token,
    get_valid_security_token,
)


router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)


def _log_delivery_failure(event: str, error: EmailDeliveryError) -> None:
    logger.error(
        "EMAIL_DELIVERY_EVENT event=%s outcome=failed provider=postmark "
        "http_status=%s provider_error_code=%s provider_message=%s",
        event,
        error.status_code,
        error.postmark_error_code,
        error.postmark_message,
    )


def _account_page(
    request: Request,
    user: User,
    *,
    error: str | None = None,
    message: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="account_settings.html",
        context={"user": user, "error": error, "message": message},
        status_code=status_code,
    )


def validate_account_name(first_name: str, last_name: str) -> tuple[str, str, str | None]:
    first_name = first_name.strip()
    last_name = last_name.strip()
    if not first_name or not last_name:
        return first_name, last_name, "First and last name are required."
    if len(first_name) > 100 or len(last_name) > 100:
        return (
            first_name,
            last_name,
            "First and last name must be 100 characters or fewer.",
        )
    return first_name, last_name, None


@router.get("/account", response_class=HTMLResponse)
async def account_settings(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active or user.email_verified_at is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)
        return _account_page(
            request,
            user,
            message=request.query_params.get("message"),
        )


@router.post("/account/profile", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    first_name, last_name, name_error = validate_account_name(
        first_name, last_name
    )

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active or user.email_verified_at is None:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        if name_error:
            return _account_page(
                request,
                user,
                error=name_error,
                status_code=400,
            )

        user.first_name = first_name
        user.last_name = last_name
        session.commit()
        log_security_event(request, "profile_update", "succeeded", user_id=user.id)
        return _account_page(
            request,
            user,
            message="Your name has been updated.",
        )


@router.post("/account/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        if not verify_password(current_password, user.password_hash):
            log_security_event(
                request,
                "password_change",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="invalid_current_password",
            )
            return _account_page(
                request,
                user,
                error="The current password is incorrect.",
                status_code=400,
            )
        if new_password != confirm_password:
            return _account_page(
                request,
                user,
                error="New passwords do not match.",
                status_code=400,
            )
        password_error = validate_password(new_password)
        if password_error:
            return _account_page(
                request, user, error=password_error, status_code=400
            )
        if verify_password(new_password, user.password_hash):
            return _account_page(
                request,
                user,
                error="Choose a password different from your current password.",
                status_code=400,
            )

        user.password_hash = hash_password(new_password)
        active_reset_tokens = session.execute(
            select(UserSecurityToken).where(
                UserSecurityToken.user_id == user.id,
                UserSecurityToken.purpose == PASSWORD_RESET,
                UserSecurityToken.used_at.is_(None),
            )
        ).scalars().all()
        for token in active_reset_tokens:
            consume_security_token(token)
        email = user.email
        session.commit()

        log_security_event(request, "password_change", "succeeded", user_id=user.id)
        try:
            send_password_changed_email(email)
        except EmailDeliveryError as exc:
            _log_delivery_failure("password_changed_notice", exc)

        return _account_page(
            request,
            user,
            message="Your password has been changed.",
        )


@router.post("/account/email", response_class=HTMLResponse)
async def request_email_change(
    request: Request,
    new_email: str = Form(...),
    current_password: str = Form(...),
):
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=303)

    try:
        new_email = validate_email(
            new_email.strip(), check_deliverability=False
        ).normalized.lower()
    except EmailNotValidError:
        new_email = ""

    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            request.session.clear()
            return RedirectResponse("/login", status_code=303)

        if not verify_password(current_password, user.password_hash):
            log_security_event(
                request,
                "email_change_request",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="invalid_current_password",
            )
            return _account_page(
                request,
                user,
                error="The current password is incorrect.",
                status_code=400,
            )
        if not new_email or new_email == user.email:
            return _account_page(
                request,
                user,
                error="Enter a different valid email address.",
                status_code=400,
            )
        duplicate = session.execute(
            select(User).where(User.email == new_email, User.id != user.id)
        ).scalar_one_or_none()
        if duplicate is not None:
            log_security_event(
                request,
                "email_change_request",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="email_unavailable",
            )
            return _account_page(
                request,
                user,
                error="That email address cannot be used.",
                status_code=400,
            )

        user.pending_email = new_email
        raw_token = create_security_token(
            session, user.id, EMAIL_CHANGE, EMAIL_CHANGE_LIFETIME
        )
        try:
            send_email_change_verification(new_email, raw_token)
            session.commit()
        except EmailDeliveryError as exc:
            session.rollback()
            _log_delivery_failure("email_change_verification", exc)
            return _account_page(
                request,
                user,
                error=(
                    "We could not send a verification email. "
                    "Your login email was not changed. Please try again later."
                ),
                status_code=503,
            )

        log_security_event(
            request, "email_change_request", "succeeded", user_id=user.id
        )
        return _account_page(
            request,
            user,
            message=(
                "A confirmation link was sent to the new address. "
                "Continue using your current email until the new one is confirmed."
            ),
        )


@router.get("/confirm-email-change/{token}", response_class=HTMLResponse)
async def confirm_email_change(request: Request, token: str):
    with SessionLocal() as session:
        security_token = get_valid_security_token(session, token, EMAIL_CHANGE)
        if security_token is None:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Invalid or Expired Link",
                    "paragraphs": [
                        "Sign in and request another email-change link."
                    ],
                    "login_link": True,
                },
                status_code=400,
            )

        user = session.get(User, security_token.user_id)
        if user is None or not user.is_active or not user.pending_email:
            return RedirectResponse("/login", status_code=303)

        conflict = session.execute(
            select(User).where(
                User.email == user.pending_email,
                User.id != user.id,
            )
        ).scalar_one_or_none()
        if conflict is not None:
            consume_security_token(security_token)
            user.pending_email = None
            session.commit()
            log_security_event(
                request,
                "email_change",
                "denied",
                level=logging.WARNING,
                user_id=user.id,
                reason="email_became_unavailable",
            )
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Unable to Change Email",
                    "paragraphs": [
                        "That email address can no longer be used."
                    ],
                    "login_link": True,
                },
                status_code=400,
            )

        old_email = user.email
        user.email = user.pending_email
        user.pending_email = None
        user.email_verified_at = datetime.now(timezone.utc)
        consume_security_token(security_token)
        session.commit()
        log_security_event(request, "email_change", "succeeded", user_id=user.id)

        try:
            send_email_changed_notice(old_email)
        except EmailDeliveryError as exc:
            _log_delivery_failure("email_changed_notice", exc)

    request.session.clear()
    return RedirectResponse("/login?email_changed=1", status_code=303)
