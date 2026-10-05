"""Provider-independent connections, explicit mappings, and OAuth state."""
from alembic import op
import sqlalchemy as sa

revision = "a61f0d8c3b92"
down_revision = "84ac2e7b91d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("integration_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("external_account_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("access_token_encrypted", sa.Text()),
        sa.Column("refresh_token_encrypted", sa.Text()),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True), nullable=name not in {"created_at", "updated_at"}) for name in
          ("expires_at", "refreshed_at", "verified_at", "created_at", "updated_at")],
        sa.UniqueConstraint("organization_id", "provider", "environment", "external_account_id", name="uq_integration_account"),
        sa.UniqueConstraint("provider", "environment", "external_account_id", name="uq_integration_account_owner"),
        sa.CheckConstraint("environment IN ('sandbox', 'production')", name="ck_integration_environment"))
    op.create_index("ix_integration_connections_organization_id", "integration_connections", ["organization_id"])
    op.create_table("integration_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("integration_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("organization_stores.id", ondelete="CASCADE")),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id", ondelete="CASCADE")),
        sa.UniqueConstraint("connection_id", "kind", "external_id", name="uq_integration_external_mapping"),
        sa.UniqueConstraint("connection_id", "store_id", name="uq_integration_store_mapping"),
        sa.UniqueConstraint("connection_id", "employee_id", name="uq_integration_employee_mapping"),
        sa.CheckConstraint("(kind = 'location' AND store_id IS NOT NULL AND employee_id IS NULL) OR (kind = 'employee' AND employee_id IS NOT NULL AND store_id IS NULL)", name="ck_integration_mapping_target"))
    op.create_index("ix_integration_mappings_connection_id", "integration_mappings", ["connection_id"])
    op.create_table("integration_oauth_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_table("integration_oauth_attempts")
    op.drop_table("integration_mappings")
    op.drop_table("integration_connections")
