"""add_packets_unconsumed_to_processing_runs

Counts packets copied into a game's inbound folder that the game never ingested.
Additive and nullable-safe: existing rows read as 0, which is the healthy value.

Revision ID: c41d8f2a9b17
Revises: a503b07f0cf7
Create Date: 2026-09-10 10:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "c41d8f2a9b17"
down_revision = "a503b07f0cf7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Checks for existence first for idempotency (SQLite compatible).
    if not _column_exists("processing_runs", "packets_unconsumed"):
        op.execute(
            "ALTER TABLE processing_runs "
            "ADD COLUMN packets_unconsumed INTEGER DEFAULT 0"
        )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode.
    with op.batch_alter_table("processing_runs") as batch_op:
        batch_op.drop_column("packets_unconsumed")


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))
