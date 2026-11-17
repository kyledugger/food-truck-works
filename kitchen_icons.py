"""Shared, deployment-owned artwork. Rules persist IDs, never image URLs."""
FOOD_ICONS = [
    {"id": "ice-cream-taco", "label": "Ice cream taco",
     "url": "/static/food-icons/ice-cream-taco-v1.png"},
    {"id": "iced-coffee", "label": "Iced coffee",
     "url": "/static/food-icons/iced-coffee-v1.png"},
    {"id": "soda-drink", "label": "Soda drink",
     "url": "/static/food-icons/soda-drink-v1.png"},
    {"id": "ice-cream-bar", "label": "Ice cream bar",
     "url": "/static/food-icons/ice-cream-bar-v1.png"},
    {"id": "frozen-banana", "label": "Frozen banana",
     "url": "/static/food-icons/frozen-banana-v1.png"},
    {"id": "shave-ice", "label": "Shave ice",
     "url": "/static/food-icons/shave-ice-v1.png"},
]
FOOD_ICON_IDS = frozenset(icon["id"] for icon in FOOD_ICONS)
