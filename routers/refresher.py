"""Periodic organization-level Poynt token refresher.

The refresher is intentionally independent of employee/user accounts. One
Poynt connection exists per organization, so each organization is refreshed
at most once per pass.
"""

import asyncio
import logging
import os

from memory_diagnostics import memory_span
from poynt.client import PoyntClient, PoyntReauthorizationRequired
from poynt.connection import get_all_poynt_connections, PoyntCredentials

logger = logging.getLogger(__name__)


async def refresh_all_poynt_connections_once() -> None:
    connections = get_all_poynt_connections()
    logger.info("Poynt refresher: checking %d organization connection(s).", len(connections))

    for connection in connections:
        credentials = PoyntCredentials(
            business_id=connection.business_id,
            access_token=connection.access_token,
            refresh_token=connection.refresh_token,
            token_type=connection.token_type,
            expires_at=connection.expires_at,
        )

        client = PoyntClient(credentials, organization_id=connection.organization_id)
        try:
            with memory_span("poynt_token_refresh", organization_id=connection.organization_id):
                await client.refresh()
        except PoyntReauthorizationRequired:
            logger.warning(
                "Poynt refresher: organization_id=%s requires reauthorization.",
                connection.organization_id,
            )
        except Exception:
            logger.exception(
                "Poynt refresher: failed for organization_id=%s.",
                connection.organization_id,
            )


async def run_poynt_refresher() -> None:
    interval = int(os.getenv("POYNT_REFRESHER_INTERVAL_SECONDS", "900"))
    logger.info("Poynt refresher started; interval=%d seconds.", interval)

    while True:
        try:
            await refresh_all_poynt_connections_once()
        except Exception:
            logger.exception("Poynt refresher pass failed.")
        await asyncio.sleep(interval)
