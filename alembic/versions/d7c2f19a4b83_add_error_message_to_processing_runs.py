"""add_error_message_to_processing_runs

Repairs a second gap between the models and the migration chain, found by
tests/test_migrations.py.

`ProcessingRun.error_message` has been in the models and in every deployed
database for a long time -- it is how a failed run explains itself, and the R-1
work reads it -- but no migration ever created it. `5e5855c5acbb` builds
processing_runs without it. Deployed databases have the column because it arrived
through `Base.metadata.create_all`, not through alembic.

So this is not a new feature; it is the chain catching up with reality. On every
existing database the guard makes it a no-op.

Revision ID: d7c2f19a4b83
Revises: c41d8f2a9b17
Create Date: 2026-09-11

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "d7c2f19a4b83"
down_revision = "c41d8f2a9b17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _column_exists("processing_runs", "error_message"):
        op.execute("ALTER TABLE processing_runs ADD COLUMN error_message TEXT")


def downgrade() -> None:
    with op.batch_alter_table("processing_runs") as batch_op:
        batch_op.drop_column("error_message")


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))
