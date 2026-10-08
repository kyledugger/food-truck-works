"""Synthetic kitchen tickets only: never POS orders, sales or tip records."""
import hashlib
from datetime import timedelta
from uuid import uuid4
from kitchen_models import KitchenTicket, KitchenAction
from store_time import utc_now


def add_samples(session, store, business_id, count, scenario, actor):
    now = utc_now()
    names = ["Alex", "Jordan", "Sam", "Taylor", "Morgan"]
    menu = ["Chocolate ice cream bar", "Frozen banana", "Shave ice", "Custom energy drink", "Cold brew"]
    for index in range(count):
        token = uuid4().hex
        variant = int(token[:8], 16) if count == 1 else index
        rows = 12 if scenario == "large" else 1 + variant % 5
        items = []
        for row in range(rows):
            items.append({"key": hashlib.sha256(f"{token}:{row}".encode()).hexdigest(),
                "provider_id": None, "sku": "TEST", "name": menu[row % len(menu)],
                "quantity": 1 + row % 3, "state": "available", "claimed_at": None, "done_at": None,
                "modifiers": [{"attribute": "Options", "values": ["Caffeine free", "Blue raspberry"]}]
                    if row % 5 == 3 else [{"attribute": "Toppings", "values": ["Chocolate dip", "Sprinkles"]}]
                    if row % 5 == 0 else []})
        ticket = KitchenTicket(organization_id=store.organization_id, store_id=store.id,
            business_id=business_id, order_id=f"test-{token}", number=f"TEST-{token[:6].upper()}",
            created_at=now-timedelta(seconds=(count-index-1)*45), updated_at=now,
            state="active", revision=1, items=items, customer_name=names[variant % len(names)],
            notes="No coconut please" if variant % 3 == 0 else None)
        session.add(ticket)
        session.flush()
        session.add(KitchenAction(ticket_id=ticket.id, at=now, action="test_arrived", revision=1,
            actor_user_id=actor, details={"simulation": True}))
