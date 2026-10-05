"""Organization-scoped usernames replace display emails."""
from alembic import op
import sqlalchemy as sa

revision = "e25a7c03b914"
down_revision = "d14f6b92a803"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE account_type = 'store_display'")).scalar():
        raise RuntimeError("Retire existing email-based display accounts before this migration. Personal accounts are unchanged.")
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("display_login_code", sa.String(32)))
        batch.create_unique_constraint("uq_org_display_login_code", ["display_login_code"])
    op.get_bind().execute(sa.text("UPDATE organizations SET display_login_code = 'ftw-' || CAST(id AS VARCHAR)"))
    with op.batch_alter_table("organization_members") as batch:
        batch.add_column(sa.Column("display_username", sa.String(50)))
        batch.create_unique_constraint("uq_org_display_username", ["organization_id", "display_username"])
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", existing_type=sa.String(320), nullable=True)
        batch.create_check_constraint("ck_person_email_required", "account_type != 'person' OR email IS NOT NULL")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")).scalar():
        raise RuntimeError("Retire username-based display accounts before downgrading.")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_person_email_required", type_="check")
        batch.alter_column("email", existing_type=sa.String(320), nullable=False)
    with op.batch_alter_table("organization_members") as batch:
        batch.drop_constraint("uq_org_display_username", type_="unique")
        batch.drop_column("display_username")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("uq_org_display_login_code", type_="unique")
        batch.drop_column("display_login_code")
