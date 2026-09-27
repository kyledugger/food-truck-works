"""add pending user email

Revision ID: e7b2c4d91a60
Revises: d4a1f6c82b30
"""

from alembic import op
import sqlalchemy as sa


revision = "e7b2c4d91a60"
down_revision = "d4a1f6c82b30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("pending_email", sa.String(length=320), nullable=True),
    )


def downgrade():
    op.drop_column("users", "pending_email")
