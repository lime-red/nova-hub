"""add_claim_links

Single-use links that hand a BBS its client credentials, instead of an admin
sending the secret over chat. Only the token's hash is stored; the secret is
generated when the link is claimed. See backend/services/claim_links.py.

Revision ID: b81c4f2d9a63
Revises: a7d3e9f15b20
Create Date: 2026-10-05 13:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b81c4f2d9a63"
down_revision = "a7d3e9f15b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if _table_exists("claim_links"):
        return
    op.create_table(
        "claim_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_by", sa.String(length=50), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("used_ip", sa.String(length=45), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claim_links_id", "claim_links", ["id"])
    op.create_index("ix_claim_links_client_id", "claim_links", ["client_id"])
    op.create_index("ix_claim_links_token_hash", "claim_links", ["token_hash"], unique=True)


def downgrade() -> None:
    if not _table_exists("claim_links"):
        return
    op.drop_index("ix_claim_links_token_hash", table_name="claim_links")
    op.drop_index("ix_claim_links_client_id", table_name="claim_links")
    op.drop_index("ix_claim_links_id", table_name="claim_links")
    op.drop_table("claim_links")


def _table_exists(table: str) -> bool:
    from sqlalchemy import inspect
    return table in inspect(op.get_bind()).get_table_names()
