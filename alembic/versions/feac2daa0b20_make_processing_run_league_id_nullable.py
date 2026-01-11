"""make_processing_run_league_id_nullable

Revision ID: feac2daa0b20
Revises: b2dad0bf570b
Create Date: 2026-01-10 10:01:00.110260

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'feac2daa0b20'
down_revision = 'b2dad0bf570b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite doesn't support ALTER COLUMN, so we need to recreate the table
    # Since processing_runs is likely empty or minimal, we'll recreate it

    # Create temporary table with new schema
    op.execute("""
        CREATE TABLE processing_runs_new (
            id INTEGER NOT NULL PRIMARY KEY,
            league_id INTEGER,
            started_at DATETIME,
            completed_at DATETIME,
            status VARCHAR(20),
            packets_processed INTEGER,
            packets_failed INTEGER,
            exit_code INTEGER,
            stdout_log TEXT,
            stderr_log TEXT,
            dosemu_log TEXT,
            FOREIGN KEY(league_id) REFERENCES leagues (id)
        )
    """)

    # Copy existing data
    op.execute("""
        INSERT INTO processing_runs_new
        SELECT * FROM processing_runs
    """)

    # Drop old table
    op.execute("DROP TABLE processing_runs")

    # Rename new table
    op.execute("ALTER TABLE processing_runs_new RENAME TO processing_runs")

    # Recreate indexes
    op.create_index('ix_processing_runs_id', 'processing_runs', ['id'])
    op.create_index('ix_processing_runs_started_at', 'processing_runs', ['started_at'])


def downgrade() -> None:
    # Reverse: make league_id NOT NULL again
    op.execute("""
        CREATE TABLE processing_runs_new (
            id INTEGER NOT NULL PRIMARY KEY,
            league_id INTEGER NOT NULL,
            started_at DATETIME,
            completed_at DATETIME,
            status VARCHAR(20),
            packets_processed INTEGER,
            packets_failed INTEGER,
            exit_code INTEGER,
            stdout_log TEXT,
            stderr_log TEXT,
            dosemu_log TEXT,
            FOREIGN KEY(league_id) REFERENCES leagues (id)
        )
    """)

    op.execute("""
        INSERT INTO processing_runs_new
        SELECT * FROM processing_runs
        WHERE league_id IS NOT NULL
    """)

    op.execute("DROP TABLE processing_runs")
    op.execute("ALTER TABLE processing_runs_new RENAME TO processing_runs")
    op.create_index('ix_processing_runs_id', 'processing_runs', ['id'])
    op.create_index('ix_processing_runs_started_at', 'processing_runs', ['started_at'])
