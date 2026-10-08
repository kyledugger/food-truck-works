"""Synthetic kitchen tickets only: never POS orders, sales or tip records."""
import hashlib
from datetime import timedelta
from uuid import uuid4
from kitchen_models import KitchenTicket, KitchenAction
from store_time import utc_now
from order_preparation import modifiers


def add_samples(session, store, business_id, count, scenario, actor):
    now = utc_now()
    names = ["Alex", "Jordan", "Sam", "Taylor", "Morgan"]
    menu = ["Shark Bite Energy Drink", "Custom Energy Drink", "Custom Shave Ice", "Chocolate ice cream bar", "Frozen banana", "Cold brew"]
    # Exact preparation fields from the supplied Poynt order. Use the real
    # modifier parser rather than a separately invented display representation.
    shark_bite = {"selectedVariants": [{"sku": "N-SHRKBT", "selectableVariations": [
        {"attribute": "Options", "values": [{"name": "Caffeine_Free"}]}]}]}
    custom_drink = {"selectedVariants": [{"sku": "N-CSTM", "selectableVariations": [
        {"attribute": "Drink_Flavors", "values": [{"name": "Blue_Raspberry"}, {"name": "Coconut"}]}]}]}
    custom_shave_ice = {"selectedVariants": [{"selectableVariations": [
        {"attribute": "Flavors", "values": [{"name": "Blue_Raspberry"},
            {"name": "Tiger_blood"}, {"name": "Wedding_Cake"}]}]}]}
    for index in range(count):
        token = uuid4().hex
        variant = int(token[:8], 16) if count == 1 else index
        rows = 12 if scenario == "large" else 1 + variant % 5
        items = []
        for row in range(rows):
            items.append({"key": hashlib.sha256(f"{token}:{row}".encode()).hexdigest(),
                "provider_id": None, "sku": "N-SHRKBT" if row % 6 == 0 else "N-CSTM" if row % 6 == 1 else "TEST", "name": menu[row % len(menu)],
                "quantity": 1 + row % 3, "state": "available", "claimed_at": None, "done_at": None,
                "modifiers": modifiers(shark_bite) if row % 6 == 0 else modifiers(custom_drink) if row % 6 == 1 else modifiers(custom_shave_ice) if row % 6 == 2 else
                    [{"attribute": "Toppings", "values": ["Chocolate dip", "Sprinkles"]}]
                    if row % 6 == 3 else []})
        ticket = KitchenTicket(organization_id=store.organization_id, store_id=store.id,
            business_id=business_id, order_id=f"test-{token}", number=f"TEST-{token[:6].upper()}",
            created_at=now-timedelta(seconds=(count-index-1)*45), updated_at=now,
            state="active", revision=1, items=items, customer_name=names[variant % len(names)],
            notes="No coconut please" if variant % 3 == 0 else None)
        session.add(ticket)
        session.flush()
        session.add(KitchenAction(ticket_id=ticket.id, at=now, action="test_arrived", revision=1,
            actor_user_id=actor, details={"simulation": True}))
