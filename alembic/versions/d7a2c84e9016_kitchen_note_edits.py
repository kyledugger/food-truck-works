"""FTW kitchen note and customer-name overrides."""
from alembic import op
import sqlalchemy as sa

revision = "d7a2c84e9016"
down_revision = "a4fdd72edc57"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("kitchen_tickets", sa.Column("kitchen_notes", sa.Text(), nullable=True))
    op.add_column("kitchen_tickets", sa.Column("kitchen_customer_name", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("kitchen_tickets", "kitchen_customer_name")
    op.drop_column("kitchen_tickets", "kitchen_notes")
