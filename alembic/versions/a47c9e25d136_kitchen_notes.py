"""Keep customer callout notes on durable kitchen tickets."""
from alembic import op
import sqlalchemy as sa

revision = "a47c9e25d136"
down_revision = "f36b8d14c025"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("kitchen_tickets", sa.Column("notes", sa.Text(), nullable=True))

def downgrade():
    op.drop_column("kitchen_tickets", "notes")
