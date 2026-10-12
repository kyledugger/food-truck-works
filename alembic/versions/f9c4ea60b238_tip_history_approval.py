"""Tip split approval audit and throttling."""
from alembic import op
import sqlalchemy as sa
revision="f9c4ea60b238"
down_revision="e8b3d95fa127"
branch_labels=None
depends_on=None


def upgrade():
    op.add_column("tip_store_settings",sa.Column("require_split_approval",sa.Boolean(),nullable=False,server_default=sa.false()))
    with op.batch_alter_table("tip_submissions") as batch:
        batch.add_column(sa.Column("approved_by_user_id",sa.Integer(),nullable=True))
        batch.create_foreign_key("fk_tip_approver","users",["approved_by_user_id"],["id"])
        batch.add_column(sa.Column("approved_at",sa.DateTime(timezone=True),nullable=True))
        batch.add_column(sa.Column("approval_digest",sa.String(64),nullable=True))
    op.create_table("tip_approval_throttles",sa.Column("key",sa.String(64),primary_key=True),
        sa.Column("window_at",sa.DateTime(timezone=True),nullable=False),sa.Column("attempts",sa.Integer(),nullable=False))


def downgrade():
    op.drop_table("tip_approval_throttles")
    with op.batch_alter_table("tip_submissions") as batch:
        batch.drop_column("approval_digest")
        batch.drop_column("approved_at")
        batch.drop_constraint("fk_tip_approver",type_="foreignkey")
        batch.drop_column("approved_by_user_id")
    op.drop_column("tip_store_settings","require_split_approval")
