"""Persistent anonymous kitchen preparation queue."""
from alembic import op
import sqlalchemy as sa

revision = "f36b8d14c025"
down_revision = "e25a7c03b914"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("kitchen_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("organization_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False), sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("number", sa.String(100), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(20), nullable=False), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.UniqueConstraint("organization_id", "business_id", "order_id", name="uq_kitchen_order"))
    op.create_index("ix_kitchen_queue", "kitchen_tickets", ["store_id", "business_id", "state", "created_at"])
    op.create_table("kitchen_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("kitchen_tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False), sa.Column("action", sa.String(30), nullable=False),
        sa.Column("item_key", sa.String(64)), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("details", sa.JSON(), nullable=False))
    op.create_index("ix_kitchen_actions_ticket_id", "kitchen_actions", ["ticket_id"])


def downgrade():
    op.drop_table("kitchen_actions")
    op.drop_table("kitchen_tickets")
