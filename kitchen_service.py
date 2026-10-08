"""Synchronize POS rows and atomically apply kitchen transitions."""
import copy
import hashlib
import json
from collections import defaultdict
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from models import OrganizationStore
from kitchen_models import KitchenTicket, KitchenAction
from live_dashboard_metrics import completed, number
from order_preparation import modifiers
from store_time import utc_now, utc_iso, as_utc, local_day_bounds


def lines(payload):
    occurrences = defaultdict(int)
    result = []
    for item in payload.get("items") or []:
        if item.get("status") in {"RETURNED", "CANCELLED"} or number(item.get("quantity")) <= 0:
            continue
        identity = str(item.get("id") or json.dumps([item.get("sku"), item.get("name")], ensure_ascii=True))
        occurrences[identity] += 1
        key = hashlib.sha256((identity+":"+str(occurrences[identity])).encode()).hexdigest()
        result.append({"key": key, "provider_id": str(item.get("id")) if item.get("id") else None,
                       "name": str(item.get("name") or item.get("sku") or "Unnamed item"),
                       "sku": str(item.get("sku") or ""), "quantity": float(number(item.get("quantity"))),
                       "modifiers": modifiers(item),
                       "state": "available", "claimed_at": None, "done_at": None})
    return result


def ticket_state(items):
    return "ready" if items and all(i["state"] == "done" for i in items) else "active"


def sync_ticket(session, order, now=None):
    now = now or utc_now()
    store = session.scalar(select(OrganizationStore).where(OrganizationStore.organization_id == order.organization_id,
        OrganizationStore.store_id == order.store_id, OrganizationStore.is_active.is_(True)))
    if store is None or not store.timezone_name:
        return
    ticket = session.scalar(select(KitchenTicket).where(KitchenTicket.organization_id == order.organization_id,
        KitchenTicket.business_id == order.business_id, KitchenTicket.order_id == order.order_id)
        .execution_options(populate_existing=True).with_for_update())
    incoming = lines(order.payload)
    notes = str(order.payload.get("notes") or "") or None
    customer_name = order.payload.get("customer_name") or None
    eligible = completed(order.payload) and bool(incoming)
    if ticket is None:
        start, end = local_day_bounds(now.astimezone(ZoneInfo(store.timezone_name)).date(), ZoneInfo(store.timezone_name))
        # Historical cache loads must never reopen old orders as new kitchen work.
        if not eligible or not start <= as_utc(order.created_at) < end or as_utc(order.created_at) > now:
            return
        try:
            with session.begin_nested():
                ticket = KitchenTicket(organization_id=order.organization_id, store_id=store.id, business_id=order.business_id,
                    order_id=order.order_id, number=str(order.payload.get("orderNumber") or order.order_id)[:100],
                    created_at=as_utc(order.created_at), updated_at=now, items=incoming, notes=notes, customer_name=customer_name, state="active", revision=1)
                session.add(ticket)
                session.flush()
        except IntegrityError:
            ticket = session.scalar(select(KitchenTicket).where(KitchenTicket.organization_id == order.organization_id,
                KitchenTicket.business_id == order.business_id, KitchenTicket.order_id == order.order_id).with_for_update())
        else:
            session.add(KitchenAction(ticket_id=ticket.id, at=now, action="arrived", revision=1, details={}))
            return ticket
    previous = {i["key"]: i for i in ticket.items}
    consumed = set()
    for item in incoming:
        old = previous.get(item["key"])
        if old is None and item["provider_id"]:
            # Cache rows from before this feature omitted provider item IDs.
            # Upgrade a uniquely matching fallback row without losing progress.
            matches = [i for i in ticket.items if not i.get("provider_id") and i["key"] not in consumed
                       and (i["sku"], i["name"], i["quantity"]) == (item["sku"], item["name"], item["quantity"])]
            if len(matches) == 1:
                old = matches[0]
        if old:
            consumed.add(old["key"])
        # Quantity changes require preparing the row again; unchanged rows retain progress.
        if old and (old["quantity"], old["sku"], old["name"], old.get("modifiers", [])) == (item["quantity"], item["sku"], item["name"], item["modifiers"]):
            for field in ("state", "claimed_at", "done_at"):
                item[field] = old[field]
    state = ticket_state(incoming) if eligible else "cancelled"
    if ticket.items != incoming or ticket.state != state or ticket.store_id != store.id or ticket.notes != notes or ticket.customer_name != customer_name:
        details = {"before": ticket.items, "after": incoming, "before_state": ticket.state, "after_state": state,
                   "before_notes": ticket.notes, "after_notes": notes}
        ticket.items = incoming
        ticket.notes = notes
        ticket.customer_name = customer_name
        ticket.state = state
        ticket.store_id = store.id
        ticket.updated_at = now
        ticket.ready_at = now if state == "ready" and ticket.ready_at is None else (ticket.ready_at if state == "ready" else None)
        ticket.revision += 1
        session.add(KitchenAction(ticket_id=ticket.id, at=now, action="pos_update", revision=ticket.revision, details=details))
    return ticket


def transition(session, ticket, action, item_key, expected_revision, actor_user_id, now=None):
    if ticket.state == "cancelled":
        raise HTTPException(409, "This order was cancelled or has no preparable items.")
    if expected_revision != ticket.revision:
        raise HTTPException(409, "Order changed on another screen. Review its current state.")
    items = copy.deepcopy(ticket.items)
    targets = items if item_key is None else [i for i in items if i["key"] == item_key]
    if not targets:
        raise HTTPException(404, "Item no longer exists.")
    now = now or utc_now()
    changed = False
    for item in targets:
        state = item["state"]
        if action == "claim" and state == "available":
            item.update(state="claimed", claimed_at=utc_iso(now), done_at=None)
        elif action == "done" and state != "done":
            item.update(state="done", done_at=utc_iso(now))
        elif action == "release" and state == "claimed":
            item.update(state="available", claimed_at=None, done_at=None)
        elif action == "undo" and state == "done":
            item.update(state="available", claimed_at=None, done_at=None)
        else:
            continue
        changed = True
    if not changed:
        raise HTTPException(409, "Already claimed or no applicable items. Refresh the order.")
    new_state = ticket_state(items)
    values = dict(items=items, state=new_state, ready_at=now if new_state == "ready" else None,
                  updated_at=now, revision=expected_revision+1)
    # Compare-and-swap protects simultaneous taps, including across processes.
    result = session.execute(update(KitchenTicket).where(KitchenTicket.id == ticket.id,
        KitchenTicket.revision == expected_revision).values(**values).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, "Order changed on another screen. Refresh and try again.")
    session.add(KitchenAction(ticket_id=ticket.id, at=now, action=action, item_key=item_key,
        revision=expected_revision+1, actor_user_id=actor_user_id, details={"before": ticket.items, "after": items}))
    return expected_revision+1


def serialize(ticket):
    return {"id": ticket.id, "number": ticket.number, "created_at": utc_iso(ticket.created_at),
            "ready_at": utc_iso(ticket.ready_at), "state": ticket.state, "revision": ticket.revision, "items": ticket.items,
            "notes": ticket.notes, "customer_name": ticket.customer_name}
