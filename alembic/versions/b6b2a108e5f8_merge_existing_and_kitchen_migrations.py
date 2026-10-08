"""merge existing and kitchen migrations

Revision ID: b6b2a108e5f8
Revises: af017c82d641, b58d0f36e247
Create Date: 2026-10-08 00:04:55.861311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6b2a108e5f8'
down_revision: Union[str, Sequence[str], None] = ('af017c82d641', 'b58d0f36e247')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
