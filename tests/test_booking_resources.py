import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from booking_resources import ensure_intrinsic_resource
from models import BookingResource, OrganizationStore


class BookingResourceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        OrganizationStore.__table__.create(self.engine)
        BookingResource.__table__.create(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_mobile_resource_is_created_once_and_keeps_its_identity(self):
        with Session(self.engine) as session:
            store = OrganizationStore(organization_id=1, store_id="truck-1", poynt_name="Our Truck",
                                      store_type="food_truck", is_active=True)
            session.add(store)
            ensure_intrinsic_resource(store)
            session.commit()
            resource_id = store.booking_resource.id

            store.display_name = "The Blue Truck"
            ensure_intrinsic_resource(store)
            session.commit()
            self.assertEqual(store.booking_resource.id, resource_id)
            self.assertEqual(store.booking_resource.name, "The Blue Truck")
            self.assertEqual(store.booking_resource.capacity, 1)
            self.assertEqual(len(session.scalars(select(BookingResource)).all()), 1)

            store.booking_resource.name = "Party Truck"
            store.booking_resource.name_follows_store = False
            store.display_name = "Another Display Name"
            ensure_intrinsic_resource(store)
            session.commit()
            self.assertEqual(store.booking_resource.name, "Party Truck")

    def test_shop_does_not_get_resource_and_type_change_preserves_one(self):
        with Session(self.engine) as session:
            store = OrganizationStore(organization_id=1, store_id="shop-1", poynt_name="Shop",
                                      store_type="shop", is_active=True)
            session.add(store)
            ensure_intrinsic_resource(store)
            session.commit()
            self.assertIsNone(store.booking_resource)

            store.store_type = "cart"
            ensure_intrinsic_resource(store)
            session.commit()
            resource_id = store.booking_resource.id
            store.store_type = "shop"
            ensure_intrinsic_resource(store)
            session.commit()
            self.assertEqual(store.booking_resource.id, resource_id)


if __name__ == "__main__":
    unittest.main()
