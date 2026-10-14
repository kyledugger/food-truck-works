"""Organization-wide kitchen item and modifier markers."""
from alembic import op
import sqlalchemy as sa

revision = "a04d61e72c39"
down_revision = "f9c4ea60b238"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("kitchen_markers",
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("kitchen_markers")
