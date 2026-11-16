"""Add durable transaction-trigger inbox and budget after kitchen markers."""
from alembic import op
import sqlalchemy as sa

revision = 'f74c28a610be'
down_revision = 'a04d61e72c39'
branch_labels = None
depends_on = None


def upgrade():
    org = lambda: sa.Column('organization_id', sa.Integer(), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    timestamp = lambda name, nullable=False: sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)
    op.create_table('transaction_order_signals', sa.Column('id', sa.Integer(), primary_key=True), org(),
        *[sa.Column(n, sa.String(100), nullable=False) for n in ('business_id','store_id','transaction_id')],
        sa.Column('order_id',sa.String(100)), sa.Column('state',sa.String(20),nullable=False),
        sa.Column('attempts',sa.Integer(),nullable=False), sa.Column('generation',sa.Integer(),nullable=False),
        *[timestamp(n) for n in ('created_at','cycle_started_at','last_received_at','next_attempt_at')],
        timestamp('last_attempt_at',True),sa.Column('last_error',sa.String(100)),
        sa.UniqueConstraint('organization_id','business_id','transaction_id',name='uq_transaction_order_signal'))
    op.create_index('ix_transaction_signal_due','transaction_order_signals',['organization_id','state','next_attempt_at'])
    op.create_table('transaction_signal_receipts',sa.Column('id',sa.Integer(),primary_key=True),org(),
        sa.Column('business_id',sa.String(100),nullable=False),sa.Column('notification_id',sa.String(100),nullable=False),
        timestamp('received_at'),sa.UniqueConstraint('organization_id','business_id','notification_id',name='uq_transaction_signal_receipt'))
    op.create_table('transaction_signal_budgets',org(),sa.Column('business_id',sa.String(100),nullable=False),
        timestamp('window_started_at'),sa.Column('request_count',sa.Integer(),nullable=False),
        timestamp('backoff_until',True),sa.PrimaryKeyConstraint('organization_id'))


def downgrade():
    op.drop_table('transaction_signal_budgets')
    op.drop_table('transaction_signal_receipts')
    op.drop_index('ix_transaction_signal_due',table_name='transaction_order_signals')
    op.drop_table('transaction_order_signals')
