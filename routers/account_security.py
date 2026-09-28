import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from auth import hash_password, validate_password
from database import SessionLocal
from email_service import (
    EmailDeliveryError,
    send_password_reset_email,
    send_verification_email,
)
from models import User, UserSecurityToken
from security_logging import log_security_event
from security_tokens import (
    EMAIL_VERIFICATION,
    EMAIL_VERIFICATION_LIFETIME,
    PASSWORD_RESET,
    PASSWORD_RESET_LIFETIME,
    consume_security_token,
    create_security_token,
    get_valid_security_token,
    token_was_recently_created,
)


router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

GENERIC_RESET_MESSAGE = (
    "If an account is eligible, a password reset link will be sent. "
    "The link expires in 30 minutes. If it does not arrive within a few "
    "minutes, check your spam folder, try again, or contact support."
)
GENERIC_VERIFICATION_MESSAGE = (
    "If that address belongs to an unverified account, a new verification "
    "link will be sent. If it does not arrive within a few minutes, check "
    "your spam folder, try again, or contact support."
)


def _log_email_delivery_failure(event: str, error: EmailDeliveryError) -> None:
    logger.error(
        "EMAIL_DELIVERY_EVENT event=%s outcome=failed provider=postmark "
        "http_status=%s provider_error_code=%s provider_message=%s",
        event,
        error.status_code,
        error.postmark_error_code,
        error.postmark_message,
    )


def _send_password_reset_if_eligible(email: str) -> None:
    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None or not user.is_active:
            return
        if token_was_recently_created(session, user.id, PASSWORD_RESET):
            return
        raw_token = create_security_token(
            session, user.id, PASSWORD_RESET, PASSWORD_RESET_LIFETIME
        )
        try:
            send_password_reset_email(user.email, raw_token)
            session.commit()
        except EmailDeliveryError as exc:
            session.rollback()
            _log_email_delivery_failure("password_reset", exc)


def _send_verification_if_eligible(email: str) -> None:
    with SessionLocal() as session:
        user = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if user is None or not user.is_active or user.email_verified_at is not None:
            return
        if token_was_recently_created(session, user.id, EMAIL_VERIFICATION):
            return
        raw_token = create_security_token(
            session,
            user.id,
            EMAIL_VERIFICATION,
            EMAIL_VERIFICATION_LIFETIME,
        )
        try:
            send_verification_email(user.email, raw_token)
            session.commit()
        except EmailDeliveryError as exc:
            session.rollback()
            _log_email_delivery_failure("email_verification", exc)


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
    )


@router.post("/forgot-password", response_class=HTMLResponse)
async def request_password_reset(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
):
    email = email.strip().lower()
    background_tasks.add_task(_send_password_reset_if_eligible, email)
    log_security_event(request, "password_reset_request", "accepted")
    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Check Your Email",
            "paragraphs": [GENERIC_RESET_MESSAGE],
            "login_link": True,
        },
    )


@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str):
    with SessionLocal() as session:
        security_token = get_valid_security_token(session, token, PASSWORD_RESET)
    if security_token is None:
        return templates.TemplateResponse(
            request=request,
            name="message.html",
            context={
                "title": "Invalid or Expired Link",
                "paragraphs": ["Request a new password reset link and try again."],
                "login_link": True,
            },
            status_code=400,
        )
    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={"token": token},
    )


@router.post("/reset-password/{token}")
async def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    error = None
    if password != confirm_password:
        error = "Passwords do not match."
    else:
        error = validate_password(password)
    if error:
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"token": token, "error": error},
            status_code=400,
        )

    with SessionLocal() as session:
        security_token = get_valid_security_token(session, token, PASSWORD_RESET)
        if security_token is None:
            log_security_event(
                request,
                "password_reset",
                "denied",
                level=logging.WARNING,
                reason="invalid_or_expired_token",
            )
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Invalid or Expired Link",
                    "paragraphs": ["Request a new password reset link and try again."],
                    "login_link": True,
                },
                status_code=400,
            )

        user = session.get(User, security_token.user_id)
        if user is None or not user.is_active:
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Unable to Reset Password",
                    "paragraphs": ["Request a new password reset link and try again."],
                    "login_link": True,
                },
                status_code=400,
            )

        user.password_hash = hash_password(password)
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(timezone.utc)
        consume_security_token(security_token)
        other_tokens = session.execute(
            select(UserSecurityToken).where(
                UserSecurityToken.user_id == user.id,
                UserSecurityToken.purpose == PASSWORD_RESET,
                UserSecurityToken.used_at.is_(None),
            )
        ).scalars().all()
        for other_token in other_tokens:
            consume_security_token(other_token)
        session.commit()
        log_security_event(request, "password_reset", "succeeded", user_id=user.id)

    request.session.clear()
    return RedirectResponse("/login?password_reset=1", status_code=303)


@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(request: Request, token: str):
    with SessionLocal() as session:
        security_token = get_valid_security_token(
            session, token, EMAIL_VERIFICATION
        )
        if security_token is None:
            log_security_event(
                request,
                "email_verification",
                "denied",
                level=logging.WARNING,
                reason="invalid_or_expired_token",
            )
            return templates.TemplateResponse(
                request=request,
                name="message.html",
                context={
                    "title": "Invalid or Expired Link",
                    "paragraphs": ["Request a new verification link and try again."],
                    "login_link": True,
                },
                status_code=400,
            )
        user = session.get(User, security_token.user_id)
        if user is None or not user.is_active:
            return RedirectResponse("/login", status_code=303)
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
        consume_security_token(security_token)
        session.commit()
        log_security_event(
            request, "email_verification", "succeeded", user_id=user.id
        )
    request.session.clear()
    return RedirectResponse("/login?verified=1", status_code=303)


@router.get("/resend-verification", response_class=HTMLResponse)
async def resend_verification_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="resend_verification.html",
    )


@router.post("/resend-verification", response_class=HTMLResponse)
async def resend_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
):
    email = email.strip().lower()
    background_tasks.add_task(_send_verification_if_eligible, email)
    log_security_event(request, "email_verification_request", "accepted")
    return templates.TemplateResponse(
        request=request,
        name="message.html",
        context={
            "title": "Check Your Email",
            "paragraphs": [GENERIC_VERIFICATION_MESSAGE],
            "login_link": True,
        },
    )
