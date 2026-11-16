import os
import asyncio
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

import httpx
from memory_diagnostics import log_memory

from poynt.connection import PoyntCredentials, locked_poynt_connection
from poynt.token import refresh_access_token, PoyntRefreshRejected, PoyntTokenError

import logging
import time as monotonic_time

logger = logging.getLogger(__name__)


class PoyntAPIError(Exception):
    """Raised when a Poynt API request fails."""


class PoyntReauthorizationRequired(PoyntAPIError):
    """Raised when Poynt must be connected or explicitly reauthorized."""


class PoyntSignalAPIError(PoyntAPIError):
    def __init__(self, status_code, retry_after=60):
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"Poynt signal lookup returned HTTP {status_code}")


class PoyntClient:
    BASE_URL = "https://services.poynt.net"
    API_VERSION = "1.2"

    def __init__(
        self,
        credentials: PoyntCredentials,
        organization_id: int,
    ):
        self.organization_id = organization_id
        self.business_id = credentials.business_id
        self.access_token = credentials.access_token
        self.refresh_token = credentials.refresh_token
        self.token_type = credentials.token_type or "BEARER"
        self.expires_at = credentials.expires_at

        self.refresh_window = timedelta(
            seconds=int(
                os.getenv(
                    "POYNT_TOKEN_REFRESH_WINDOW_SECONDS",
                    "600",
                )
            )
        )

    def _api_client(self) -> httpx.AsyncClient:
        """Instrument Poynt HTTP requests without logging credentials or order data."""

        async def on_request(request: httpx.Request) -> None:
            request.extensions["ftw_started_at"] = monotonic_time.monotonic()

        async def on_response(response: httpx.Response) -> None:
            started = response.request.extensions.get("ftw_started_at")
            elapsed_ms = (
                round((monotonic_time.monotonic() - started) * 1000)
                if started is not None else None
            )
            # Headers may be absent. Log only rate-limit-related fields,
            # never Authorization, cookies, request bodies, or URL queries.
            limits = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {
                    "retry-after", "ratelimit-limit", "ratelimit-remaining",
                    "ratelimit-reset", "x-ratelimit-limit",
                    "x-ratelimit-remaining", "x-ratelimit-reset",
                }
            }
            log = logger.warning if response.status_code == 429 else logger.info
            log(
                "Poynt API request organization_id=%s method=%s path=%s "
                "status=%s duration_ms=%s rate_limit_headers=%s",
                self.organization_id,
                response.request.method,
                response.request.url.path,
                response.status_code,
                elapsed_ms,
                limits,
            )

        return httpx.AsyncClient(
            timeout=30.0,
            event_hooks={"request": [on_request], "response": [on_response]},
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "api-version": self.API_VERSION,
            "Authorization": (
                f"{self.token_type} {self.access_token}"
            ),
        }

    def _expiration_state(self) -> str:
        """
        Return one of:

        - "valid": outside the refresh window
        - "refresh": inside the refresh window
        - "expired": already expired
        """

        if not self.expires_at:
            raise PoyntAPIError(
                "Poynt access token has no expiration time."
            )

        now = datetime.now(timezone.utc)
        expires_at = self.expires_at

        # PostgreSQL may return a naive datetime depending on
        # the database column configuration.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )

        seconds_remaining = (
            expires_at - now
        ).total_seconds()

        if now >= expires_at:
            logger.info(
                "Poynt access token has expired; attempting refresh."
            )
            return "expired"

        if now + self.refresh_window >= expires_at:
            logger.info(
                "Poynt access token is within refresh window "
                "(%.1f seconds remaining); refreshing.",
                seconds_remaining,
            )
            return "refresh"

        logger.debug(
            "Poynt access token is valid and outside "
            "the refresh window (%.1f seconds remaining).",
            seconds_remaining,
        )

        return "valid"

    async def _refresh_if_needed(self) -> None:
        # Always reread under the lock, including for apparently valid clients:
        # another worker or OAuth reconnect may already have replaced this token.
        credentials = await asyncio.to_thread(self._refresh_in_worker)
        self.business_id = credentials.business_id
        self.access_token = credentials.access_token
        self.refresh_token = credentials.refresh_token
        self.token_type = credentials.token_type or "BEARER"
        self.expires_at = credentials.expires_at

    def _refresh_in_worker(self) -> PoyntCredentials:
        with locked_poynt_connection(self.organization_id) as connection:
            if connection is None:
                raise PoyntReauthorizationRequired("Poynt is not connected.")
            current = PoyntCredentials(
                connection.business_id, connection.access_token,
                connection.refresh_token, connection.token_type,
                connection.expires_at,
            )
            client = PoyntClient(current, self.organization_id)
            client.refresh_window = self.refresh_window
            asyncio.run(client._refresh_locked())
            connection.access_token = client.access_token
            connection.refresh_token = client.refresh_token
            connection.token_type = client.token_type
            connection.expires_at = client.expires_at
            result = PoyntCredentials(
                client.business_id, client.access_token, client.refresh_token,
                client.token_type, client.expires_at,
            )
        # The context commits before the caller adopts the new credentials.
        return result

    async def _refresh_locked(self) -> None:
        state = self._expiration_state()

        if state == "valid":
            logger.debug(
                "Poynt access token is outside refresh window; "
                "no refresh needed."
            )
            return

        # Refresh both near-expiration and expired access tokens.
        # An expired access token does not mean authorization was revoked.

        logger.info(
            "Poynt access token refresh starting."
        )

        if not self.refresh_token:
            raise PoyntReauthorizationRequired(
                "No Poynt refresh token is available. "
                "The merchant must reconnect Poynt."
            )

        try:
            token_response = await refresh_access_token(self.refresh_token)
        except PoyntRefreshRejected as exc:
            raise PoyntReauthorizationRequired(
                "Poynt rejected the refresh credentials. Please reconnect Poynt."
            ) from exc
        except PoyntTokenError as exc:
            raise PoyntAPIError(str(exc)) from exc

        access_token = token_response.get("accessToken")
        refresh_token = token_response.get("refreshToken")
        token_type = token_response.get("tokenType")
        expires_in = token_response.get("expiresIn")

        if not access_token:
            raise PoyntAPIError(
                "Poynt refresh response did not contain "
                "an access token."
            )

        if not refresh_token:
            raise PoyntAPIError(
                "Poynt refresh response did not contain "
                "a refresh token."
            )

        if expires_in is None:
            raise PoyntAPIError(
                "Poynt refresh response did not contain "
                "expiresIn."
            )

        try:
            if isinstance(expires_in, bool):
                raise ValueError
            expires_in = int(expires_in)
            if expires_in <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise PoyntAPIError("Poynt refresh returned invalid expiresIn.") from None

        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=int(expires_in))
        )

        logger.info(
            "Poynt access token refreshed successfully; "
            "new expiration: %s",
            expires_at,
        )        

        # Update the in-memory client first.
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_type = token_type or self.token_type
        self.expires_at = expires_at

        # The worker persists this complete set in its locked transaction.

    async def refresh(self) -> None:
        """Refresh the organization's Poynt token when it enters the refresh window."""
        await self._refresh_if_needed()

    async def get_catalogs(self) -> dict:
        await self._refresh_if_needed()

        logger.info(
            "Poynt catalog request starting."
        )        

        url = (
            f"{self.BASE_URL}"
            f"/businesses/{self.business_id}/catalogs"
        )

        async with self._api_client() as client:
            response = await client.get(
                url,
                headers=self._headers(),
            )

        if not response.is_success:
            logger.error(
                "Poynt catalog request failed: HTTP %d",
                response.status_code,
            )            
            raise PoyntAPIError(
                f"Poynt API returned HTTP "
                f"{response.status_code}"
            )

        logger.info(
            "Poynt catalog request succeeded: HTTP %d",
            response.status_code,
        )  

        return response.json()
    
    async def get_recent_orders(self, limit=100, start_at=None, end_at=None, fetch_all=False) -> list[dict]:    
        """
        Get the most recent orders for this business.

        Poynt's orders endpoint returns collections in ascending
        pagination order. We first retrieve the total order count,
        then request the final page using startOffset.
        """

        await self._refresh_if_needed()

        limit = max(1, min(limit, 100))

        url = (
            f"{self.BASE_URL}"
            f"/businesses/{self.business_id}/orders"
        )

        # First request: determine the total number of orders.
        logger.info(
            "Poynt recent orders count request starting."
        )

        count_params = {
            "limit": 1,
            "timeType": "createdAt",
        }

        if start_at:
            count_params["startAt"] = start_at

        if end_at:
            count_params["endAt"] = end_at

        async with self._api_client() as client:
            count_response = await client.get(
                url,
                headers=self._headers(),
                params=count_params,
            )

            if not count_response.is_success:
                logger.error(
                    "Poynt recent orders count request failed: "
                    "HTTP %d",
                    count_response.status_code,
                )

                raise PoyntAPIError(
                    f"Poynt API returned HTTP "
                    f"{count_response.status_code}"
                )

            count_data = count_response.json()
            total_count = int(count_data.get("count", 0))

            logger.info(
                "Poynt recent orders count received: "
                "total_orders=%d.",
                total_count,
            )

            if total_count == 0:
                return []

            # ----

            if fetch_all:
                start_offset = 0
            else:
                # Normal behavior: return only the most recent `limit` orders.
                start_offset = max(0, total_count - limit)

            log_memory("order_fetch_start", organization_id=self.organization_id, total_orders=total_count, fetch_all=fetch_all)
            all_orders = []

            while start_offset < total_count:

                page_limit = min(limit, total_count - start_offset)

                logger.info(
                    "Poynt recent orders request starting: "
                    "limit=%d, start_offset=%d, total_orders=%d, fetch_all=%s.",
                    page_limit,
                    start_offset,
                    total_count,
                    fetch_all,
                )

                params = {
                    "limit": page_limit,
                    "startOffset": start_offset,
                    "timeType": "createdAt",                    
                }

                if start_at:
                    params["startAt"] = start_at

                if end_at:
                    params["endAt"] = end_at

                response = await client.get(
                    url,
                    headers=self._headers(),
                    params=params,
                )

                if not response.is_success:
                    logger.error(
                        "Poynt recent orders request failed: HTTP %d",
                        response.status_code,
                    )

                    raise PoyntAPIError(
                        f"Poynt API returned HTTP "
                        f"{response.status_code}"
                    )

                data = response.json()
                page_orders = data.get("orders", [])

                logger.info(
                    "Poynt orders page received: "
                    "start_offset=%d, orders_received=%d.",
                    start_offset,
                    len(page_orders),
                )

                all_orders.extend(page_orders)

                if not fetch_all:
                    break

                if not page_orders:
                    break

                start_offset += len(page_orders)

                if len(page_orders) < page_limit:
                    break

            logger.info(
                "Poynt recent orders request succeeded: "
                "total_orders_returned=%d.",
                len(all_orders),
            )

            log_memory("order_fetch_end", organization_id=self.organization_id, returned_orders=len(all_orders))
            return all_orders



            #----

        if not response.is_success:
            logger.error(
                "Poynt recent orders request failed: HTTP %d",
                response.status_code,
            )

            raise PoyntAPIError(
                f"Poynt API returned HTTP "
                f"{response.status_code}"
            )

        data = response.json()
        orders = data.get("orders", [])

        logger.info(
            "Poynt recent orders request succeeded: "
            "HTTP %d, orders_received=%d.",
            response.status_code,
            len(orders),
        )

        return orders
    

    async def get_stores(self) -> dict:
        await self._refresh_if_needed()

        logger.info(
            "Poynt business request starting."
        )

        url = (
            f"{self.BASE_URL}"
            f"/businesses/{self.business_id}/stores"
        )


        async with self._api_client() as client:
            response = await client.get(
                url,
                headers=self._headers(),
            )

        if not response.is_success:
            logger.error(
                "Poynt businesses request failed: HTTP %d",
                response.status_code,
            )            
            raise PoyntAPIError(
                f"Poynt API returned HTTP "
                f"{response.status_code}"
            )

        logger.info(
            "Poynt businesses request succeeded: HTTP %d",
            response.status_code,
        )  

        print(response)


        return response.json()

    async def get_order(self, order_id: str) -> dict:
        """Fetch current state, never follow URLs from a webhook payload."""
        from urllib.parse import quote
        await self._refresh_if_needed()
        async with self._api_client() as client:
            response = await client.get(
                f"{self.BASE_URL}/businesses/{quote(self.business_id, safe='')}/orders/{quote(order_id, safe='')}",
                headers=self._headers())
        if not response.is_success:
            raise PoyntAPIError(f"Poynt API returned HTTP {response.status_code}")
        return response.json()

    async def signal_get(self, resource, resource_id=None, params=None):
        """One budgeted-by-caller GET. Never follows provider payload URLs."""
        from urllib.parse import quote
        from email.utils import parsedate_to_datetime
        if resource not in {"transactions", "orders"}:
            raise ValueError("Unexpected signal resource")
        await self._refresh_if_needed()
        path = f"/businesses/{quote(self.business_id, safe='')}/{resource}"
        if resource_id is not None:
            path += "/" + quote(resource_id, safe="")
        async with self._api_client() as client:
            response = await client.get(self.BASE_URL + path, headers=self._headers(), params=params, timeout=5)
        if not response.is_success:
            delay = 60
            header = response.headers.get("retry-after", "")
            try:
                delay = max(1, int(header))
            except ValueError:
                try:
                    delay = max(1, int((parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds())+1)
                except (ValueError, TypeError, OverflowError):
                    pass
            raise PoyntSignalAPIError(response.status_code, delay)
        data = response.json()
        if not isinstance(data, dict):
            raise PoyntAPIError("Unexpected signal response shape")
        return data

    async def register_order_webhook(self, delivery_url: str, secret: str) -> dict:
        import uuid
        await self._refresh_if_needed()
        headers = {**self._headers(), "Poynt-Request-Id": str(uuid.uuid4())}
        async with self._api_client() as client:
            response = await client.post(f"{self.BASE_URL}/hooks", headers=headers, json={
                "applicationId": os.environ["POYNT_APP_ID"], "businessId": self.business_id,
                "deliveryUrl": delivery_url, "secret": secret,
                "eventTypes": ["ORDER_OPENED", "ORDER_COMPLETED", "ORDER_CANCELLED", "ORDER_UPDATED"]})
        if not response.is_success:
            raise PoyntAPIError(f"Poynt webhook registration returned HTTP {response.status_code}")
        return response.json()
    
