"""Shared Poynt transaction history for event attribution and reconciliation."""
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
    values = {
        "organization_id": organization_id,
        "business_id": business_id,
        "transaction_id": transaction_id,
        "store_id": context.get("storeId") or store_id,
        "terminal_id": context.get("terminalId") or payload.get("terminalId"),
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


async def backfill_day(client, session_factory, organization_id, day, zone):
    """Reconcile one local day atomically; failed pulls can be rerun."""
    from store_time import local_day_bounds, utc_iso

    start, end = local_day_bounds(day, zone)
    seen = set()
    rows = []
    for offset in range(0, 10000, 100):
        page = await client.signal_get("transactions", params={
            "timeType": "createdAt",
            "startAt": utc_iso(start),
            "endAt": utc_iso(end),
            "limit": 100,
            "startOffset": offset,
        })
        batch = page.get("transactions", [] if page.get("count") == 0 else None)
        if not isinstance(batch, list):
            raise ValueError("Unexpected Poynt transaction page")
        if not batch:
            if type(page.get("count")) is int and page["count"] > len(seen):
                raise ValueError("Poynt reported more transactions than the scan retrieved")
            break
        for payload in batch:
            identifier = payload["id"]
            if identifier in seen:
                raise ValueError("Repeated transaction in paginated Poynt response")
            seen.add(identifier)
            if start <= instant(payload["createdAt"]) < end:
                rows.append(payload)
        if len(batch) < 100:
            if type(page.get("count")) is int and page["count"] > len(seen):
                raise ValueError("Poynt reported more transactions than the scan retrieved")
            break
    else:
        raise ValueError("Transaction scan reached its pagination limit")
    with session_factory.begin() as session:
        for payload in sorted(rows, key=lambda row: row["id"]):
            save_transaction(session, organization_id, client.business_id, payload)
    return len(rows)
