import csv
import hashlib
import hmac
import io
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from email_validator import validate_email, EmailNotValidError
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from database import SessionLocal
from launch_models import LaunchSubscriber, LaunchRateLimit
from launch_email import send_launch_confirmation
from email_service import EmailDeliveryError

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)
basic = HTTPBasic()
CONSENT = "launch-updates-v1"


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def page(request, message=None, status_code=200):
    request.session.setdefault("launch_csrf", secrets.token_urlsafe(32))
    return templates.TemplateResponse(request=request, name="coming_soon.html",
        context={"launch_message": message, "launch_csrf": request.session["launch_csrf"],
                 "turnstile_site_key": os.getenv("TURNSTILE_SITE_KEY", "")}, status_code=status_code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})


def check_csrf(request, token):
    expected = request.session.get("launch_csrf", "")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(403, "Please reload the page and try again.")


def throttle(request):
    # Shared DB throttle. Do not trust arbitrary forwarded client-IP headers.
    key = hmac.new(os.environ["SESSION_SECRET"].encode(),
        (request.client.host if request.client else "unknown").encode(), hashlib.sha256).hexdigest()
    stamp = now()
    with SessionLocal() as db:
        db.execute(insert(LaunchRateLimit).values(key=key, window_at=stamp, attempts=0).on_conflict_do_nothing())
        row = db.execute(select(LaunchRateLimit).where(LaunchRateLimit.key == key).with_for_update()).scalar_one()
        if row.window_at < stamp - timedelta(minutes=10):
            row.window_at, row.attempts = stamp, 0
        row.attempts += 1
        allowed = row.attempts <= 10
        db.execute(delete(LaunchRateLimit).where(LaunchRateLimit.window_at < stamp - timedelta(days=1)))
        db.commit()
    if not allowed:
        raise HTTPException(429, "Please wait a few minutes before trying again.")


@router.get("/launch-updates", response_class=HTMLResponse)
def signup_page(request: Request):
    return page(request)


@router.post("/launch-updates", response_class=HTMLResponse)
def signup(request: Request, email: str = Form(..., max_length=320),
           feedback: str = Form("", max_length=2000), consent: str = Form(""),
           csrf: str = Form(""), website: str = Form(""),
           captcha: str = Form("", alias="cf-turnstile-response")):
    check_csrf(request, csrf)
    throttle(request)
    if website:
        return page(request, "Please check your inbox to confirm your subscription.")
    if consent != "yes":
        return page(request, "Please check the box to request launch updates.", 400)
    try:
        address = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return page(request, "Please enter a valid email address.", 400)
    secret = os.getenv("TURNSTILE_SECRET_KEY", "")
    if not secret or not os.getenv("TURNSTILE_SITE_KEY"):
        return page(request, "Signup is being set up. Please try again soon.", 503)
    try:
        result = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": captcha}, timeout=10)
        result.raise_for_status()
        verified = result.json()
        from urllib.parse import urlparse
        expected_host = urlparse(os.environ["APP_BASE_URL"]).hostname
        valid = verified.get("success") and verified.get("hostname") == expected_host and verified.get("action") == "launch-signup"
    except (httpx.HTTPError, ValueError):
        valid = False
    if not valid:
        return page(request, "Please complete the spam check and try again.", 400)
    stamp = now()
    raw = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.execute(insert(LaunchSubscriber).values(email=address, feedback=feedback.strip(),
            consent_version=CONSENT, created_at=stamp, suppressed=False).on_conflict_do_nothing(index_elements=["email"]))
        row = db.execute(select(LaunchSubscriber).where(LaunchSubscriber.email == address).with_for_update()).scalar_one()
        if row.confirmed_at or row.suppressed or (row.last_sent_at and row.last_sent_at > stamp - timedelta(minutes=10)):
            return page(request, "If your address needs confirmation, please check your inbox. Already confirmed? You're on the list.")
        row.token_hash, row.token_expires_at = digest(raw), stamp + timedelta(hours=24)
        row.last_sent_at = stamp
        db.commit()
    try:
        send_launch_confirmation(address, raw)
    except (EmailDeliveryError, KeyError):
        logger.warning("Launch confirmation delivery failed; no recipient or token logged")
        return page(request, "We couldn't send the confirmation email. Please try again in 10 minutes.", 503)
    return page(request, "Please check your inbox to confirm your subscription. Thanks for helping shape Food Truck Works!")


@router.get("/launch-updates/confirm/{token}", response_class=HTMLResponse)
def confirm_page(request: Request, token: str):
    request.session.setdefault("launch_csrf", secrets.token_urlsafe(32))
    return templates.TemplateResponse(request=request, name="launch_confirm.html",
        context={"token": token, "csrf": request.session["launch_csrf"]},
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/launch-updates/confirm/{token}", response_class=HTMLResponse)
def confirm(request: Request, token: str, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as db:
        row = db.execute(select(LaunchSubscriber).where(LaunchSubscriber.token_hash == digest(token)).with_for_update()).scalar_one_or_none()
        if not row or row.suppressed or not row.token_expires_at or row.token_expires_at <= now():
            return page(request, "This confirmation link is expired or unavailable. Please sign up again.", 400)
        row.confirmed_at, row.token_hash, row.token_expires_at = now(), None, None
        db.commit()
    return page(request, "You're on the list. Thanks for helping shape Food Truck Works!")


def webhook_auth(credentials: HTTPBasicCredentials = Depends(basic)):
    username, password = os.getenv("POSTMARK_WEBHOOK_USERNAME", ""), os.getenv("POSTMARK_WEBHOOK_PASSWORD", "")
    if not username or not password or not (secrets.compare_digest(credentials.username, username) and secrets.compare_digest(credentials.password, password)):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})


@router.post("/launch-updates/postmark", dependencies=[Depends(webhook_auth)])
async def suppression_webhook(request: Request):
    if len(await request.body()) > 65536:
        raise HTTPException(413, "Payload too large")
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError()
        stream = os.getenv("POSTMARK_BROADCAST_STREAM", "")
        if data.get("RecordType") != "SubscriptionChange" or data.get("MessageStream") not in {stream, "outbound"}:
            return {"ok": True}
        address = str(data["Recipient"]).strip().lower()
        stamp = datetime.fromisoformat(data["ChangedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or not isinstance(data.get("SuppressSending"), bool):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid payload")
    with SessionLocal() as db:
        row = db.execute(select(LaunchSubscriber).where(LaunchSubscriber.email == address).with_for_update()).scalar_one_or_none()
        if row and data["SuppressSending"] and (not row.suppression_changed_at or stamp >= row.suppression_changed_at):
            row.suppressed = True
            row.suppression_reason = str(data.get("SuppressionReason") or "ManualSuppression")[:50]
            row.suppression_changed_at = stamp
            row.token_hash = None
            db.commit()
    # Provider reactivation never silently restores local marketing consent.
    return {"ok": True}


def require_admin(request):
    allowed = {int(x.strip()) for x in os.getenv("LAUNCH_ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()}
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(401, "Please log in.")
    from models import User
    with SessionLocal() as db:
        user = db.get(User, uid)
        if uid not in allowed or not user or not user.is_active:
            raise HTTPException(403, "Access denied")


@router.get("/admin/launch-updates", response_class=HTMLResponse)
def admin(request: Request):
    require_admin(request)
    with SessionLocal() as db:
        rows = db.execute(select(LaunchSubscriber).order_by(LaunchSubscriber.created_at.desc()).limit(500)).scalars().all()
        return templates.TemplateResponse(request=request, name="launch_admin.html", context={"rows": rows}, headers={"Cache-Control": "no-store"})


def safe_csv(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@router.get("/admin/launch-updates.csv")
def export(request: Request):
    require_admin(request)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "feedback", "created_at", "confirmed_at", "suppressed", "suppression_reason", "consent_version"])
    with SessionLocal() as db:
        for row in db.execute(select(LaunchSubscriber).order_by(LaunchSubscriber.id)).scalars():
            writer.writerow([safe_csv(getattr(row, field)) for field in ["email", "feedback", "created_at", "confirmed_at", "suppressed", "suppression_reason", "consent_version"]])
    return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="launch-signups.csv"', "Cache-Control": "no-store"})
