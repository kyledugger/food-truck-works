"""add user names

Revision ID: f1c3a8d72b40
Revises: e7b2c4d91a60
"""

from alembic import op
import sqlalchemy as sa


revision = "f1c3a8d72b40"
down_revision = "e7b2c4d91a60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("first_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_name", sa.String(length=100), nullable=True),
    )

    # Preserve account-level names already known through linked employees.
    op.execute(
        sa.text(
            """
            UPDATE users
            SET first_name = (
                    SELECT employees.first_name
                    FROM employees
                    WHERE employees.user_id = users.id
                    ORDER BY employees.id
                    LIMIT 1
                ),
                last_name = (
                    SELECT employees.last_name
                    FROM employees
                    WHERE employees.user_id = users.id
                    ORDER BY employees.id
                    LIMIT 1
                )
            WHERE EXISTS (
                    SELECT 1
                    FROM employees
                    WHERE employees.user_id = users.id
                )
              AND users.first_name IS NULL
              AND users.last_name IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")
