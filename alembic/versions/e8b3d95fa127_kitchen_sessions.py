"""Explicit per-store kitchen service sessions, disabled until started."""
from alembic import op
import sqlalchemy as sa

revision = "e8b3d95fa127"
down_revision = "d7a2c84e9016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("kitchen_sessions",
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("organization_stores.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("business_id", sa.String(100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("kitchen_sessions")
