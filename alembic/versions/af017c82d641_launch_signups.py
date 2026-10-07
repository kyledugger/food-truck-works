"""Launch updates subscribers and shared-worker signup throttle."""
from alembic import op
import sqlalchemy as sa
revision = "af017c82d641"
down_revision = "b81e742d9c30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("launch_subscribers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("feedback", sa.Text(), nullable=False),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suppression_reason", sa.String(50)),
        sa.Column("suppression_changed_at", sa.DateTime(timezone=True)))
    op.create_table("launch_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("launch_rate_limits")
    op.drop_table("launch_subscribers")
