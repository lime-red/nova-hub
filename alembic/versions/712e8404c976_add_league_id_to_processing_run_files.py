"""add_league_id_to_processing_run_files

Revision ID: 712e8404c976
Revises: feac2daa0b20
Create Date: 2026-02-23 01:32:11.819693

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "712e8404c976"
down_revision = "feac2daa0b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add league_id column to processing_run_files.
    # Checks for existence first for idempotency (SQLite compatible).
    if not _column_exists("processing_run_files", "league_id"):
        op.execute(
            "ALTER TABLE processing_run_files ADD COLUMN league_id INTEGER REFERENCES leagues(id)"
        )
    if not _index_exists("processing_run_files", "ix_processing_run_files_league_id"):
        op.create_index(
            "ix_processing_run_files_league_id",
            "processing_run_files",
            ["league_id"],
            unique=False,
        )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode.
    with op.batch_alter_table("processing_run_files") as batch_op:
        batch_op.drop_index("ix_processing_run_files_league_id")
        batch_op.drop_column("league_id")


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _index_exists(table: str, index_name: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return any(idx["name"] == index_name for idx in insp.get_indexes(table))
