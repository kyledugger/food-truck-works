"""Retain the Poynt employee ID separately from the device ID."""
from alembic import op
import sqlalchemy as sa

revision = "921d6a4c273e"
down_revision = "6d137420af28"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "poynt_transactions",
        sa.Column("employee_user_id", sa.String(100), nullable=True),
    )


def downgrade():
    op.drop_column("poynt_transactions", "employee_user_id")
