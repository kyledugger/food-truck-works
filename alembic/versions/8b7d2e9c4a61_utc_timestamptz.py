"""Interpret legacy UTC timestamp values as UTC instants.

Revision ID: 8b7d2e9c4a61
Revises: 6a18b9e4c532
"""
from alembic import op
import sqlalchemy as sa

revision = "8b7d2e9c4a61"
down_revision = "6a18b9e4c532"
branch_labels = None
depends_on = None

COLUMNS = {
    "users": ("created_at", "email_verified_at"),
    "user_security_tokens": ("expires_at", "used_at", "created_at"),
    "organizations": ("created_at", "updated_at"),
    "organization_members": ("created_at",),
    "employees": ("created_at", "updated_at"),
    "organization_invitations": ("expires_at", "accepted_at", "created_at"),
    "poynt_connections": ("expires_at", "created_at", "updated_at"),
    "tip_submissions": ("report_start_at", "report_end_at", "submitted_at", "processed_at"),
    "tip_store_settings": ("tip_allocation_start_at",),
    "tip_order_claims": ("created_at",),
    "tip_employee_payouts": ("paid_at",),
}


def upgrade():
    for table, columns in COLUMNS.items():
        for column in columns:
            op.alter_column(
                table, column,
                existing_type=sa.DateTime(timezone=False),
                type_=sa.DateTime(timezone=True),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )


def downgrade():
    for table, columns in reversed(list(COLUMNS.items())):
        for column in columns:
            op.alter_column(
                table, column,
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime(timezone=False),
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
            )
