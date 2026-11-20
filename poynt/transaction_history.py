"""Shared Poynt transaction history for event attribution and reconciliation."""
from datetime import timedelta
import logging
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, String, Table
from sqlalchemy.dialects.postgresql import insert

from database import Base
from instant_type import UTCInstant
from live_dashboard_metrics import instant

transactions = Table(
    "poynt_transactions", Base.metadata,
    Column("organization_id", ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
    Column("business_id", String(100), primary_key=True),
    Column("transaction_id", String(100), primary_key=True),
    Column("store_id", String(100)),
    Column("terminal_id", String(100)),
    Column("employee_user_id", String(100)),
    Column("parent_id", String(100)),
    Column("order_id", String(100)),
    Column("payment_type", String(50)),
    Column("action", String(50)),
    Column("status", String(50)),
    Column("voided", Boolean),
    Column("settled", Boolean),
    Column("currency", String(10)),
    Column("transaction_cents", BigInteger),
    Column("order_cents", BigInteger),
    Column("tip_cents", BigInteger),
    Column("tax_cents", BigInteger),
    Column("created_at", UTCInstant(), nullable=False),
    Column("updated_at", UTCInstant(), nullable=False),
    Index("ix_poynt_transactions_period", "organization_id", "business_id", "created_at"),
)


def save_transaction(session, organization_id, business_id, payload, store_id=None):
    context = payload.get("context") or {}
    amounts = payload.get("amounts") or {}
    created_at = instant(payload["createdAt"])
    transaction_id = payload["id"]
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("Missing Poynt transaction ID")
    amount_columns = {
        "transaction_cents": "transactionAmount",
        "order_cents": "orderAmount",
        "tip_cents": "tipAmount",
        "tax_cents": "taxAmount",
    }
    cents = {column: amounts.get(key) for column, key in amount_columns.items()}
    if any(value is not None and type(value) is not int for value in cents.values()):
        raise ValueError("Expected integer Poynt amounts")
    order_ids = {
        ref["id"] for ref in payload.get("references") or []
        if ref.get("type") == "POYNT_ORDER" and isinstance(ref.get("id"), str)
    }
    employee_user_id = context.get("employeeUserId")
    if type(employee_user_id) is int:
        employee_user_id = str(employee_user_id)
    elif isinstance(employee_user_id, str):
        employee_user_id = employee_user_id.strip() or None
    else:
        employee_user_id = None
    values = {
        "organization_id": organization_id,
        "business_id": business_id,
        "transaction_id": transaction_id,
        "store_id": context.get("storeId") or store_id,
        "terminal_id": context.get("storeDeviceId") or context.get("terminalId") or payload.get("terminalId"),
        "employee_user_id": employee_user_id,
        "parent_id": payload.get("parentId"),
        "order_id": next(iter(order_ids)) if len(order_ids) == 1 else None,
        "payment_type": (payload.get("fundingSource") or {}).get("type"),
        "action": payload.get("action"),
        "status": payload.get("status"),
        "voided": payload.get("voided"),
        "settled": payload.get("settled"),
        "currency": amounts.get("currency"),
        "created_at": created_at,
        "updated_at": instant(payload["updatedAt"]) if payload.get("updatedAt") else created_at,
        **cents,
    }
    statement = insert(transactions).values(**values)
    updates = {
        column: statement.excluded[column]
        for column, value in values.items()
        if column not in {"organization_id", "business_id", "transaction_id"} and value is not None
    }
    session.execute(statement.on_conflict_do_update(
        index_elements=list(transactions.primary_key.columns),
        set_=updates,
        where=transactions.c.updated_at <= statement.excluded.updated_at,
    ))


class IncompleteTransactionScan(ValueError):
    def __init__(self, rows, retrieved, reported):
        self.rows = rows
        self.reported = reported
        super().__init__(f"{retrieved} unique rows, reported total {reported}, no next link")


async def scan_transactions(client, start, end, *, require_count_match=False):
    from store_time import utc_iso

    seen = set()
    rows = []
    expected_count = None
    cursors = set()
    params = {
        "timeType": "createdAt",
        "startAt": utc_iso(start),
        "endAt": utc_iso(end),
        "limit": 100,
    }
    for _ in range(100):
        page = await client.signal_get("transactions", params=params)
        batch = page.get("transactions", [] if page.get("count") == 0 else None)
        if not isinstance(batch, list) or len(batch) > 100:
            raise ValueError("Unexpected Poynt transaction page")
        count = page.get("count")
        if type(count) is int:
            expected_count = max(expected_count or 0, count)
        next_links = [link for link in page.get("links") or [] if link.get("rel") == "next"]
        for payload in batch:
            identifier = payload["id"]
            if identifier in seen:
                raise ValueError("Repeated transaction in paginated Poynt response")
            seen.add(identifier)
            if start <= instant(payload["createdAt"]) < end:
                rows.append(payload)
        if not next_links:
            count_short = expected_count is not None and len(seen) < expected_count
            count_unverified = require_count_match and len(rows) != expected_count
            if count_short or count_unverified or (count is None and len(batch) == 100):
                raise IncompleteTransactionScan(rows, len(seen), expected_count)
            break
        if not batch or len(next_links) != 1:
            raise ValueError("Invalid Poynt continuation")
        # Keep the configured API endpoint and original date bounds.
        query = parse_qs(urlsplit(next_links[0].get("href") or "").query)
        tokens = query.get("startFrom", [])
        if len(tokens) != 1 or tokens[0] in cursors:
            raise ValueError("Missing or repeated Poynt startFrom cursor")
        cursor = tokens[0]
        cursors.add(cursor)
        params = {**params, "startFrom": cursor}
    else:
        raise ValueError("Transaction scan reached its pagination limit")
    return rows


async def backfill_day(client, session_factory, organization_id, day, zone):
    """Persist a day only after all of its Poynt windows reconcile."""
    from store_time import local_day_bounds

    start, end = local_day_bounds(day, zone)
    reported_mismatch = None
    try:
        rows = await scan_transactions(client, start, end)
    except IncompleteTransactionScan as daily_error:
        daily_ids = {row["id"] for row in daily_error.rows}
        hourly_rows = []
        lower = start
        while lower < end:
            upper = min(lower + timedelta(hours=1), end)
            try:
                hourly_rows.extend(await scan_transactions(
                    client, lower, upper, require_count_match=True,
                ))
            except IncompleteTransactionScan as hourly_error:
                raise ValueError(
                    f"Incomplete Poynt scan for {day}: hour starting "
                    f"{lower.astimezone(zone):%H:%M} ({hourly_error}). No transactions written."
                ) from None
            lower = upper
        hourly_ids = {row["id"] for row in hourly_rows}
        if hourly_ids != daily_ids or len(hourly_ids) != len(hourly_rows):
            raise ValueError(
                f"Incomplete Poynt scan for {day}: "
                f"{len(hourly_ids - daily_ids)} hourly-only IDs, "
                f"{len(daily_ids - hourly_ids)} full-day-only IDs, "
                f"{len(hourly_rows) - len(hourly_ids)} repeated hourly IDs. "
                "No transactions written."
            ) from None
        rows = hourly_rows
        reported_mismatch = str(daily_error)
    with session_factory.begin() as session:
        for payload in sorted(rows, key=lambda row: row["id"]):
            save_transaction(session, organization_id, client.business_id, payload)
    if reported_mismatch:
        logging.getLogger(__name__).warning(
            "%s: full-day Poynt count differed (%s). "
            "Hourly counts reconciled and IDs matched. Saved %s transactions.",
            day, reported_mismatch, len(rows),
        )
    return len(rows)
