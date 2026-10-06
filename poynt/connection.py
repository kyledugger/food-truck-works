from dataclasses import dataclass
from datetime import datetime
from contextlib import contextmanager
from threading import Lock

from database import SessionLocal
from models import PoyntConnection

import logging

logger = logging.getLogger(__name__)

# Bounded local locks also serialize SQLite development sessions. PostgreSQL's
# row lock below provides coordination across processes and app instances.
_refresh_locks = [Lock() for _ in range(64)]


@contextmanager
def locked_poynt_connection(organization_id: int):
    """Hold the connection row until refresh and persistence have finished.

    Call from a worker thread: acquiring a database lock can block.
    """
    with _refresh_locks[organization_id % len(_refresh_locks)]:
        with SessionLocal() as session:
            with session.begin():
                connection = session.query(PoyntConnection).filter(
                    PoyntConnection.organization_id == organization_id
                ).with_for_update().one_or_none()
                yield connection


@dataclass
class PoyntCredentials:
    business_id: str
    access_token: str
    refresh_token: str | None
    token_type: str | None
    expires_at: datetime | None


def get_poynt_connection(organization_id: int) -> PoyntConnection | None:
    """
    Return the Poynt connection belonging to an organization.

    Returns None if the organization has not connected Poynt.
    """

    with SessionLocal() as session:
        return session.query(PoyntConnection).filter(
            PoyntConnection.organization_id == organization_id
        ).one_or_none()


def get_poynt_credentials(
    organization_id: int
) -> PoyntCredentials | None:
    """
    Retrieve the Poynt credentials associated with an organization.

    Returns None if the organization has not connected Poynt.
    """

    connection = get_poynt_connection(organization_id)

    if not connection:
        return None

    return PoyntCredentials(
        business_id=connection.business_id,
        access_token=connection.access_token,
        refresh_token=connection.refresh_token,
        token_type=connection.token_type,
        expires_at=connection.expires_at,
    )


def get_all_poynt_connections() -> list[PoyntConnection]:
    """Return all organization-owned Poynt connections."""
    with SessionLocal() as session:
        return session.query(PoyntConnection).order_by(
            PoyntConnection.organization_id
        ).all()


def save_poynt_connection(
    organization_id: int,
    business_id: str,
    access_token: str,
    refresh_token: str | None,
    token_type: str | None,
    expires_at: datetime | None,
) -> None:
    """
    Create or update the Poynt connection for an organization.
    """

    with SessionLocal() as session:

        connection = session.query(PoyntConnection).filter(
            PoyntConnection.organization_id == organization_id
        ).one_or_none()

        if connection:
            connection.business_id = business_id
            connection.access_token = access_token
            connection.refresh_token = refresh_token
            connection.token_type = token_type
            connection.expires_at = expires_at
            logger.info(
                "Updated existing Poynt connection for organization_id=%d",
                organization_id,
            )

        else:
            connection = PoyntConnection(
                organization_id=organization_id,
                business_id=business_id,
                access_token=access_token,
                refresh_token=refresh_token,
                token_type=token_type,
                expires_at=expires_at,
            )
            logger.info(
                "Created new Poynt connection for organization_id=%d",
                organization_id,
            )

            session.add(connection)

        session.commit()
