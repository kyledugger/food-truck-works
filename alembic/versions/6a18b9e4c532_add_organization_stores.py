"""Add organization Poynt store configuration.

Revision ID: 6a18b9e4c532
Revises: 4e7c90a81b62
"""
from alembic import op
import sqlalchemy as sa

revision = "6a18b9e4c532"
down_revision = "4e7c90a81b62"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "organization_stores" not in inspector.get_table_names():
        op.create_table(
            "organization_stores",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("store_id", sa.String(100), nullable=False),
            sa.Column("poynt_name", sa.String(200), nullable=False),
            sa.Column("display_name", sa.String(200), nullable=True),
            sa.Column("timezone_name", sa.String(100), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("organization_id", "store_id", name="uq_organization_store"),
        )
        op.create_index("ix_organization_stores_organization_id", "organization_stores", ["organization_id"])
        return

    # main.py calls Base.metadata.create_all() at startup. If the application
    # started with the new model before Alembic ran, the table already exists.
    # Accept only that exact model-created shape; never silently skip a
    # different table merely because it has the same name.
    columns = {column["name"]: column for column in inspector.get_columns("organization_stores")}
    expected = {
        "id": (sa.Integer, False, None),
        "organization_id": (sa.Integer, False, None),
        "store_id": (sa.String, False, 100),
        "poynt_name": (sa.String, False, 200),
        "display_name": (sa.String, True, 200),
        "timezone_name": (sa.String, True, 100),
        "is_active": (sa.Boolean, False, None),
    }
    if set(columns) != set(expected):
        raise RuntimeError("Existing organization_stores table has unexpected columns; inspect it before migrating.")
    for name, (kind, nullable, length) in expected.items():
        actual = columns[name]
        if (not isinstance(actual["type"], kind) or actual["nullable"] != nullable
                or (length is not None and actual["type"].length != length)):
            raise RuntimeError(f"Existing organization_stores.{name} differs from the application model.")
    if inspector.get_pk_constraint("organization_stores").get("constrained_columns") != ["id"]:
        raise RuntimeError("Existing organization_stores primary key differs from the application model.")
    uniques = inspector.get_unique_constraints("organization_stores")
    if not any(set(item["column_names"]) == {"organization_id", "store_id"} for item in uniques):
        raise RuntimeError("Existing organization_stores is missing the organization/store unique constraint.")
    foreign_keys = inspector.get_foreign_keys("organization_stores")
    if not any(item["constrained_columns"] == ["organization_id"]
               and item["referred_table"] == "organizations" for item in foreign_keys):
        raise RuntimeError("Existing organization_stores has an unexpected organization foreign key.")
    indexes = inspector.get_indexes("organization_stores")
    if not any(item["name"] == "ix_organization_stores_organization_id"
               and item["column_names"] == ["organization_id"] for item in indexes):
        op.create_index("ix_organization_stores_organization_id", "organization_stores", ["organization_id"])


def downgrade():
    op.drop_index("ix_organization_stores_organization_id", table_name="organization_stores")
    op.drop_table("organization_stores")
