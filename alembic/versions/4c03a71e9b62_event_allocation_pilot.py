"""Add events and transaction claims for the historical allocation pilot."""
from alembic import op
import sqlalchemy as sa

revision = "4c03a71e9b62"
down_revision = "921d6a4c273e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "allocation_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("county", sa.String(100), nullable=False),
    )
    op.create_table(
        "allocation_claims",
        sa.Column("organization_id", sa.Integer, primary_key=True),
        sa.Column("business_id", sa.String(100), primary_key=True),
        sa.Column("transaction_id", sa.String(100), primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("allocation_events.id"), nullable=False),
        sa.Column("claimed_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "business_id", "transaction_id"],
            ["poynt_transactions.organization_id", "poynt_transactions.business_id",
             "poynt_transactions.transaction_id"],
        ),
    )


def downgrade():
    op.drop_table("allocation_claims")
    op.drop_table("allocation_events")
