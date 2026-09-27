"""add store tip allocation cutoff and employee submission window

Revision ID: 4e7c90a81b62
Revises: 2b9e51c73a04
"""

from alembic import op
import sqlalchemy as sa


revision = "4e7c90a81b62"
down_revision = "2b9e51c73a04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tip_store_settings", sa.Column("tip_allocation_start_at", sa.DateTime(), nullable=True))
    op.add_column("tip_store_settings", sa.Column("employee_submission_hours", sa.Integer(), nullable=False, server_default="24"))
    op.create_check_constraint(
        "ck_tip_employee_submission_hours", "tip_store_settings",
        "employee_submission_hours BETWEEN 1 AND 720",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tip_employee_submission_hours", "tip_store_settings", type_="check")
    op.drop_column("tip_store_settings", "employee_submission_hours")
    op.drop_column("tip_store_settings", "tip_allocation_start_at")
