"""Separate customer names from order instructions on kitchen tickets."""
from alembic import op
import sqlalchemy as sa
revision = "b58d0f36e247"
down_revision = "a47c9e25d136"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("kitchen_tickets", sa.Column("customer_name", sa.Text(), nullable=True))
def downgrade():
    op.drop_column("kitchen_tickets", "customer_name")
