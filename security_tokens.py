import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import UserSecurityToken
from store_time import as_utc


EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"
EMAIL_CHANGE = "email_change"
EMAIL_VERIFICATION_LIFETIME = timedelta(hours=24)
PASSWORD_RESET_LIFETIME = timedelta(minutes=30)
EMAIL_CHANGE_LIFETIME = timedelta(hours=24)
EMAIL_RESEND_COOLDOWN = timedelta(minutes=5)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_security_token(
    session: Session,
    user_id: int,
    purpose: str,
    lifetime: timedelta,
) -> str:
    now = datetime.now(timezone.utc)
    active_tokens = session.execute(
        select(UserSecurityToken).where(
            UserSecurityToken.user_id == user_id,
            UserSecurityToken.purpose == purpose,
            UserSecurityToken.used_at.is_(None),
        )
    ).scalars().all()
    for token in active_tokens:
        token.used_at = now

    raw_token = secrets.token_urlsafe(32)
    session.add(
        UserSecurityToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_token(raw_token),
            expires_at=now + lifetime,
        )
    )
    return raw_token


def get_valid_security_token(
    session: Session,
    raw_token: str,
    purpose: str,
) -> UserSecurityToken | None:
    token = session.execute(
        select(UserSecurityToken).where(
            UserSecurityToken.token_hash == hash_token(raw_token),
            UserSecurityToken.purpose == purpose,
        )
    ).scalar_one_or_none()
    if token is None or token.used_at is not None:
        return None
    if as_utc(token.expires_at) <= datetime.now(timezone.utc):
        return None
    return token


def consume_security_token(token: UserSecurityToken) -> None:
    token.used_at = datetime.now(timezone.utc)


def token_was_recently_created(
    session: Session,
    user_id: int,
    purpose: str,
    cooldown: timedelta = EMAIL_RESEND_COOLDOWN,
) -> bool:
    latest = session.execute(
        select(UserSecurityToken)
        .where(
            UserSecurityToken.user_id == user_id,
            UserSecurityToken.purpose == purpose,
        )
        .order_by(UserSecurityToken.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return bool(latest and as_utc(latest.created_at) > datetime.now(timezone.utc) - cooldown)
