"""Store tenant webhook registration and per-store notification preferences."""
from alembic import op
import sqlalchemy as sa

revision = 'b13d82e69a04'
down_revision = 'f74c28a610be'
branch_labels = None
depends_on = None


def upgrade():
    org = lambda: sa.Column('organization_id',sa.Integer(),sa.ForeignKey('organizations.id',ondelete='CASCADE'),nullable=False)
    op.create_table('poynt_transaction_webhooks',sa.Column('id',sa.Integer(),primary_key=True),org(),
        sa.Column('business_id',sa.String(100),nullable=False),
        sa.Column('application_id',sa.String(100),nullable=False),
        sa.Column('delivery_url',sa.String(500),nullable=False),
        sa.Column('secret_encrypted',sa.Text(),nullable=False),
        sa.Column('hook_id',sa.String(100)),sa.Column('status',sa.String(20),nullable=False),
        sa.Column('error',sa.String(200)),
        sa.Column('next_check_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('verified_at',sa.DateTime(timezone=True)),
        sa.Column('last_received_at',sa.DateTime(timezone=True)),
        sa.Column('lease_token',sa.String(64)),sa.Column('lease_until',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('organization_id','business_id',name='uq_transaction_webhook_business'))
    op.create_table('poynt_transaction_store_settings',sa.Column('id',sa.Integer(),primary_key=True),org(),
        sa.Column('business_id',sa.String(100),nullable=False),
        sa.Column('store_id',sa.String(100),nullable=False),sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.UniqueConstraint('organization_id','business_id','store_id',name='uq_transaction_store_setting'))


def downgrade():
    op.drop_table('poynt_transaction_store_settings')
    op.drop_table('poynt_transaction_webhooks')
