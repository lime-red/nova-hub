"""add_processing_run_items

One row per data item a game moved between two nodes, read from the /DETAILED
transcript. Purely additive: a new table, no change to any existing one, so a
rollback loses the movement history and nothing else.

The table exists because retention drops ProcessingRun.dosemu_log after 30 days.
Parsing on demand would work for a month and then quietly show an empty page.

Revision ID: a7d4e2f91b58
Revises: f3b8d41e6c72
Create Date: 2026-09-17 09:40:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7d4e2f91b58"
down_revision = "f3b8d41e6c72"
branch_labels = None
depends_on = None

TABLE = "processing_run_items"


def upgrade() -> None:
    if _table_exists(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("processing_run_id", sa.Integer(),
                  sa.ForeignKey("processing_runs.id"), nullable=False),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("leagues.id"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("item_type", sa.String(length=40), nullable=False),
        sa.Column("src_node", sa.Integer(), nullable=True),
        sa.Column("dst_node", sa.Integer(), nullable=True),
        sa.Column("size_before", sa.Integer(), nullable=True),
        sa.Column("size_after", sa.Integer(), nullable=True),
        sa.Column("phase", sa.String(length=60), nullable=True),
    )
    # (ix_..._id comes from the id column's index=True, so it is not repeated.)
    # The two shapes the admin view asks for: one run's items, and a league's
    # recent traffic. Everything else is a filter on top of those.
    op.create_index(f"ix_{TABLE}_processing_run_id", TABLE, ["processing_run_id"])
    op.create_index(f"ix_{TABLE}_league_occurred", TABLE, ["league_id", "occurred_at"])
    op.create_index(f"ix_{TABLE}_item_type", TABLE, ["item_type"])


def downgrade() -> None:
    if _table_exists(TABLE):
        op.drop_table(TABLE)


def _table_exists(table: str) -> bool:
    from sqlalchemy import inspect
    return inspect(op.get_bind()).has_table(table)
