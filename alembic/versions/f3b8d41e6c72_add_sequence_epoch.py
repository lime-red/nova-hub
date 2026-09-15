"""Add sequence_epoch to sequence_alerts

An alert is identified by its route and the number the missing packet would have
carried. That was unique until the numbering went round: production's busiest
route has now been round six times, so "missing 992" names six different packets
and the de-duplication check cannot tell a fresh loss from a stale alert for the
same number two cycles ago.

The epoch says which time round it was. Nullable, because every existing row
pre-dates the column and there is no honest value to invent for it -- those rows
were raised when the detector could not see past one cycle at all.

Revision ID: f3b8d41e6c72
Revises: e4a1c9b27d30
Create Date: 2026-09-15
"""

import sqlalchemy as sa
from alembic import op

revision = "f3b8d41e6c72"
down_revision = "e4a1c9b27d30"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Whether the column is already there.

    Guarded in both directions because this database has been stamped by hand
    more than once, and a migration that cannot be re-run is a migration that
    strands a deployment.
    """
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    if not _column_exists("sequence_alerts", "sequence_epoch"):
        op.add_column(
            "sequence_alerts",
            sa.Column("sequence_epoch", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("sequence_alerts", "sequence_epoch"):
        op.drop_column("sequence_alerts", "sequence_epoch")
