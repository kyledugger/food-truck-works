"""Shared, deployment-owned artwork. Rules persist IDs, never image URLs."""
FOOD_ICONS = [
    {"id": "ice-cream-bar", "label": "Ice cream bar",
     "url": "/static/food-icons/ice-cream-bar-v1.png"},
]
FOOD_ICON_IDS = frozenset(icon["id"] for icon in FOOD_ICONS)
