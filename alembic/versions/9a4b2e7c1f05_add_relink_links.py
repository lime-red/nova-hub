"""add_relink_links

Single-use links that connect a provider sign-in to an existing hub account.
See backend/services/relink_links.py.

Revision ID: 9a4b2e7c1f05
Revises: 6d2f8a41c9e7
Create Date: 2026-10-10 14:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "9a4b2e7c1f05"
down_revision = "6d2f8a41c9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if _table_exists("relink_links"):
        return
    op.create_table(
        "relink_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_by", sa.String(length=50), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_ip", sa.String(length=45), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["sysop_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relink_links_id", "relink_links", ["id"])
    op.create_index("ix_relink_links_user_id", "relink_links", ["user_id"])
    op.create_index("ix_relink_links_token_hash", "relink_links", ["token_hash"], unique=True)


def downgrade() -> None:
    if _table_exists("relink_links"):
        op.drop_table("relink_links")


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()
