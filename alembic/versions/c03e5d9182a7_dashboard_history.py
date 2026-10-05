"""On-demand historical dashboard date loads."""
from alembic import op
import sqlalchemy as sa

revision = "c03e5d9182a7"
down_revision = "b92d7a10e643"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("dashboard_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("business_id", sa.String(100), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("loaded_at", sa.DateTime(timezone=True)),
        sa.Column("next_load_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(200)),
        sa.UniqueConstraint("organization_id", "business_id", "report_date", name="uq_dashboard_day"))
    op.create_index("ix_dashboard_day_pending", "dashboard_days", ["next_load_at", "requested_at"])


def downgrade():
    op.drop_table("dashboard_days")
