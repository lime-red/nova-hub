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
    # processing_run_files is not created by any earlier migration. It was added
    # to the models in b1ade4d and brought into existing databases by the one-off
    # migrate_add_processing_files.py, which calls Base.metadata.create_all -- so
    # it never entered the alembic chain at all. On a database that has only ever
    # been migrated, the table simply does not exist and this revision used to die
    # with NoSuchTableError, making a fresh install impossible by the documented
    # path (`alembic upgrade head`).
    #
    # Creating it here rather than in a new head revision is deliberate: the chain
    # is linear, so a fresh database reaches THIS revision long before it reaches
    # the head, and the failure has to be repaired where it happens. For any
    # database that already has the table -- every deployed one -- this block is a
    # no-op, so re-running an applied revision stays safe.
    if not _table_exists("processing_run_files"):
        op.create_table(
            "processing_run_files",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("processing_run_id", sa.Integer(), nullable=False),
            sa.Column("file_type", sa.String(length=20), nullable=False),
            sa.Column("filename", sa.String(length=100), nullable=False),
            sa.Column("file_data", sa.Text(), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["processing_run_id"], ["processing_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_processing_run_files_id", "processing_run_files", ["id"])
        op.create_index(
            "ix_processing_run_files_file_type", "processing_run_files", ["file_type"]
        )
        op.create_index(
            "ix_processing_run_files_created_at", "processing_run_files", ["created_at"]
        )

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


def _table_exists(table: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    return inspect(bind).has_table(table)


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
