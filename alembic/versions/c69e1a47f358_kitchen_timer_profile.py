"""Shared per-store kitchen timer thresholds."""
from alembic import op
import sqlalchemy as sa
revision = "c69e1a47f358"
down_revision = "b58d0f36e247"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("kitchen_timer_profiles",
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("organization_stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("green_seconds", sa.Integer(), nullable=False),
        sa.Column("yellow_seconds", sa.Integer(), nullable=False),
        sa.Column("red_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("green_seconds >= 0 AND green_seconds < yellow_seconds AND yellow_seconds < red_seconds AND red_seconds <= 86400", name="ck_kitchen_timer_bands"))


def downgrade():
    op.drop_table("kitchen_timer_profiles")
