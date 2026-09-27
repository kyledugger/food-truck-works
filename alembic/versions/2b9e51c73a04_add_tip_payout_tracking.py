"""add store tip policies, order claims, and employee payouts

Revision ID: 2b9e51c73a04
Revises: f1c3a8d72b40
"""

from alembic import op
import sqlalchemy as sa


revision = "2b9e51c73a04"
down_revision = "f1c3a8d72b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tip_store_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("store_id", sa.String(length=100), nullable=False),
        sa.Column("payout_policy", sa.String(length=20), nullable=False, server_default="choice"),
        sa.Column("cash_confirmation", sa.String(length=20), nullable=False, server_default="authorized"),
        sa.Column("cash_confirmer_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["cash_confirmer_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "store_id", name="uq_tip_store_settings_org_store"),
        sa.CheckConstraint("payout_policy IN ('cash', 'paycheck', 'choice')", name="ck_tip_store_payout_policy"),
        sa.CheckConstraint("cash_confirmation IN ('self', 'authorized')", name="ck_tip_store_cash_confirmation"),
    )
    op.create_table(
        "tip_order_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("poynt_business_id", sa.String(length=100), nullable=False),
        sa.Column("poynt_order_id", sa.String(length=100), nullable=False),
        sa.Column("store_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("tip_cents", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["submission_id"], ["tip_submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "poynt_business_id", "poynt_order_id", name="uq_tip_order_once"),
    )
    op.create_index("ix_tip_order_claims_submission_id", "tip_order_claims", ["submission_id"])
    op.create_table(
        "tip_employee_payouts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("employee_name", sa.String(length=200), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("payout_method", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("paid_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmation_mode", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], ["tip_submissions.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["paid_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("submission_id", "employee_id", name="uq_tip_payout_employee_submission"),
        sa.CheckConstraint("payout_method IN ('cash', 'paycheck')", name="ck_tip_employee_payout_method"),
        sa.CheckConstraint("status IN ('pending', 'paid', 'rejected')", name="ck_tip_employee_payout_status"),
    )
    op.create_index("ix_tip_employee_payouts_submission_id", "tip_employee_payouts", ["submission_id"])


def downgrade() -> None:
    op.drop_index("ix_tip_employee_payouts_submission_id", table_name="tip_employee_payouts")
    op.drop_table("tip_employee_payouts")
    op.drop_index("ix_tip_order_claims_submission_id", table_name="tip_order_claims")
    op.drop_table("tip_order_claims")
    op.drop_table("tip_store_settings")
