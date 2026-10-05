"""Dedicated store displays and reusable store-scoped assignments."""
from alembic import op
import sqlalchemy as sa

revision = "d14f6b92a803"
down_revision = "c03e5d9182a7"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("account_type", sa.String(30), nullable=False, server_default="person"))
        batch.create_check_constraint("ck_user_account_type", "account_type IN ('person', 'store_display')")
    op.create_table("store_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_member_id", sa.Integer(), sa.ForeignKey("organization_members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_store_id", sa.Integer(), sa.ForeignKey("organization_stores.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('store_display', 'store_manager', 'staff')", name="ck_store_assignment_role"),
        sa.UniqueConstraint("organization_member_id", "organization_store_id", name="uq_store_assignment_member_store"))
    op.create_index("ix_store_assignments_organization_member_id", "store_assignments", ["organization_member_id"])
    op.create_index("ix_store_assignments_organization_store_id", "store_assignments", ["organization_store_id"])
    op.create_index("uq_store_display_member", "store_assignments", ["organization_member_id"], unique=True,
        postgresql_where=sa.text("role = 'store_display'"), sqlite_where=sa.text("role = 'store_display'"))


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE account_type = 'store_display'")).scalar():
        raise RuntimeError("Remove dedicated Store Display accounts before downgrading; they must not become personal accounts.")
    op.drop_table("store_assignments")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_user_account_type", type_="check")
        batch.drop_column("account_type")
