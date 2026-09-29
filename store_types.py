"""Organization store categories; booking availability is a separate concept."""

STORE_TYPES = {
    "food_truck": "Food truck",
    "food_trailer": "Food trailer",
    "cart": "Cart",
    "pop_up": "Pop-up (tent or booth)",
    "shop": "Shop (brick and mortar)",
    "catering": "Catering",
}

INTRINSIC_BOOKABLE_TYPES = frozenset({"food_truck", "food_trailer", "cart", "pop_up"})
