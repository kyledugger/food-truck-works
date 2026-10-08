"""merge existing and kitchen migrations

Revision ID: a4fdd72edc57
Revises: b6b2a108e5f8, c69e1a47f358
Create Date: 2026-10-08 00:58:18.677048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4fdd72edc57'
down_revision: Union[str, Sequence[str], None] = ('b6b2a108e5f8', 'c69e1a47f358')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
