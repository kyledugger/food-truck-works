import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import logging

logger = logging.getLogger(__name__)


class PoyntTokenError(Exception):
    """Token request failed without proof that merchant consent was revoked."""


class PoyntTokenTemporaryError(PoyntTokenError):
    """A later refresher pass may retry this request."""


class PoyntRefreshRejected(PoyntTokenError):
    """The refresh endpoint explicitly rejected the refresh credentials."""


def _check_refresh_response(response: httpx.Response) -> dict:
    # Never log response bodies or free-text messages: they may contain tokens.
    if not response.is_success:
        codes = []
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            for key in ("error", "code", "errorCode"):
                value = body.get(key)
                if isinstance(value, str):
                    codes.append(value)
                elif isinstance(value, dict):
                    for field in ("code", "errorCode"):
                        if isinstance(value.get(field), str):
                            codes.append(value[field])
        rejected = {"invalid_grant", "INVALID_REFRESH_TOKEN"}
        recognized = rejected | {
            "invalid_client", "unauthorized_client", "invalid_request",
            "unsupported_grant_type", "temporarily_unavailable", "server_error",
        }
        safe_code = next((c for c in codes if c in recognized), "unrecognized")
        status = response.status_code
        logger.warning("Poynt refresh failed: HTTP %d; code=%s", status, safe_code)
        # Status takes precedence: never mark consent revoked for rate limits
        # or provider failures, even when a misleading body contains a code.
        if status in (408, 425, 429) or status >= 500:
            raise PoyntTokenTemporaryError(
                f"Poynt refresh temporarily failed (HTTP {status}); retry later."
            )
        if status in (400, 401, 403) and any(c in rejected for c in codes):
            raise PoyntRefreshRejected("Poynt rejected the refresh credentials.")
        raise PoyntTokenError(
            f"Poynt refresh failed (HTTP {status}; code={safe_code}); "
            "authorization rejection was not confirmed."
        )
    try:
        body = response.json()
    except ValueError:
        raise PoyntTokenError("Poynt refresh returned invalid JSON.") from None
    if not isinstance(body, dict):
        raise PoyntTokenError("Poynt refresh returned an unexpected response.")
    return body


PRIVATE_KEY_PATH = (
    Path(__file__).resolve().parent.parent
    / "jwt"
    / "poynt_private_key.pem"
)


PRIVATE_KEY_PATH = Path(
    os.getenv(
        "POYNT_PRIVATE_KEY_PATH",
        Path(__file__).resolve().parent.parent
        / "jwt"
        / "poynt_private_key.pem"
    )
)


def load_private_key() -> str:
    if not PRIVATE_KEY_PATH.exists():
        raise FileNotFoundError(
            f"Poynt private key not found at {PRIVATE_KEY_PATH}"
        )

    return PRIVATE_KEY_PATH.read_text(encoding="utf-8")


def create_self_signed_jwt() -> str:
    poynt_app_id = os.environ["POYNT_APP_ID"]    
    now = datetime.now(timezone.utc)

    payload = {
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "iss": poynt_app_id,
        "sub": poynt_app_id,
        "aud": "https://services.poynt.net",
        "jti": str(uuid.uuid4()),
    }

    private_key = load_private_key()

    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
    )


async def exchange_authorization_code(
    code: str,
    redirect_uri: str,
) -> dict:

    poynt_app_id = os.environ["POYNT_APP_ID"]
    poynt_token_url = os.environ["POYNT_TOKEN_URL"]

    self_signed_jwt = create_self_signed_jwt()

    headers = {
        "Accept": "application/json",
        "api-version": "1.2",
        "Authorization": f"Bearer {self_signed_jwt}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": poynt_app_id,
        "redirect_uri": redirect_uri,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            poynt_token_url,
            headers=headers,
            data=data,
        )

    if not response.is_success:
        logger.error(
            "Poynt token request failed: HTTP %d",
            response.status_code,
        )

        response.raise_for_status()

    return response.json()



async def refresh_access_token(
    refresh_token: str,
) -> dict:
    """
    Refresh an active Poynt authorization.

    Poynt's refresh flow uses the existing refresh token and does
    not require the application's self-signed JWT.
    """

    poynt_token_url = os.environ["POYNT_TOKEN_URL"]

    headers = {
        "Accept": "application/json",
        "api-version": "1.2",
        "Content-Type": "application/x-www-form-urlencoded",
        "Poynt-Request-Id": str(uuid.uuid4()),
    }

    data = {
        "grantType": "REFRESH_TOKEN",
        "refreshToken": refresh_token,
    }

    logger.info("Refreshing Poynt access token with refresh token" )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                poynt_token_url, headers=headers, data=data,
            )
    except httpx.RequestError:
        logger.warning("Poynt refresh transport failed; retry later.")
        raise PoyntTokenTemporaryError(
            "Poynt refresh could not reach the provider; retry later."
        ) from None

    return _check_refresh_response(response)
