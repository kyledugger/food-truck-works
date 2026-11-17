"""Cache separate dashboard payments and exact order-payment identity links."""
from alembic import op
import sqlalchemy as sa

revision='c27a90e51f68'
down_revision='b13d82e69a04'
branch_labels=None
depends_on=None


def upgrade():
    org=lambda:sa.Column('organization_id',sa.Integer(),sa.ForeignKey('organizations.id',ondelete='CASCADE'),nullable=False)
    stamp=lambda name,nullable=False:sa.Column(name,sa.DateTime(timezone=True),nullable=nullable)
    op.create_table('additional_payment_days',sa.Column('id',sa.Integer(),primary_key=True),org(),
        sa.Column('business_id',sa.String(100),nullable=False),sa.Column('report_date',sa.Date(),nullable=False),
        *[stamp(n) for n in ('start_at','end_at','requested_at','next_sync_at')],
        stamp('loaded_at',True),stamp('scan_started_at',True),sa.Column('cursor',sa.Integer(),nullable=False),
        sa.Column('phase',sa.String(20),nullable=False),sa.Column('seen_order_ids',sa.JSON(),nullable=False),
        sa.Column('staged',sa.JSON(),nullable=False),sa.Column('snapshot',sa.JSON(),nullable=False),
        sa.Column('error',sa.String(200)),
        sa.UniqueConstraint('organization_id','business_id','report_date',name='uq_additional_payment_day'))
    op.create_table('additional_payment_controls',org(),sa.Column('business_id',sa.String(100),nullable=False),
        stamp('next_request_at'),sa.PrimaryKeyConstraint('organization_id'))
    op.create_table('dashboard_payment_links',sa.Column('id',sa.Integer(),primary_key=True),org(),
        sa.Column('business_id',sa.String(100),nullable=False),sa.Column('order_id',sa.String(100),nullable=False),
        sa.Column('transaction_id',sa.String(100),nullable=False),
        sa.UniqueConstraint('organization_id','business_id','order_id','transaction_id',name='uq_dashboard_payment_link'))
    op.create_index('ix_dashboard_payment_transaction','dashboard_payment_links',['organization_id','business_id','transaction_id'])


def downgrade():
    op.drop_index('ix_dashboard_payment_transaction',table_name='dashboard_payment_links')
    op.drop_table('dashboard_payment_links')
    op.drop_table('additional_payment_controls')
    op.drop_table('additional_payment_days')
