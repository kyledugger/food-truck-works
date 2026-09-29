"""Keep the initial bookable unit implicit for mobile store types."""

from models import BookingResource, OrganizationStore
from store_types import INTRINSIC_BOOKABLE_TYPES


def store_booking_name(store: OrganizationStore) -> str:
    return store.display_name or store.poynt_name


def ensure_intrinsic_resource(store: OrganizationStore) -> None:
    """Call within the store save transaction; preserve custom names and stable IDs."""
    resource = store.booking_resource
    if store.store_type in INTRINSIC_BOOKABLE_TYPES:
        if resource is None:
            store.booking_resource = BookingResource(
                name=store_booking_name(store), name_follows_store=True,
                is_enabled=True, capacity=1,
            )
        else:
            resource.is_enabled = True
            resource.capacity = 1
            if resource.name_follows_store:
                resource.name = store_booking_name(store)
    elif resource is not None and resource.name_follows_store:
        resource.name = store_booking_name(store)
