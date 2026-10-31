"""client_league_polls

When each client last asked for each league's packets, and what software it
said it was. Until now the per-league poll was only in the access log, and
clients.last_seen moves with every token whatever the league, so a league a
client never polls (eots and 015F, Oct 2026) looked like any other.

Nothing to backfill: a league reads as "not polled since" this migration.

Revision ID: c4e8a1f2b6d3
Revises: a9d4e2f7c815
Create Date: 2026-10-31 13:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c4e8a1f2b6d3"
down_revision = "a9d4e2f7c815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_league_polls",
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), primary_key=True),
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("leagues.id"), primary_key=True),
        sa.Column("first_poll_at", sa.DateTime(), nullable=False),
        sa.Column("last_poll_at", sa.DateTime(), nullable=False),
        sa.Column("last_user_agent", sa.String(length=100), nullable=True),
    )
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("last_user_agent", sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("last_user_agent")
    op.drop_table("client_league_polls")
