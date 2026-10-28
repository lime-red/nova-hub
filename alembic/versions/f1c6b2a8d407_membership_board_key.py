"""membership_board_key

An Immortal Barons board signs its packets with an ed25519 key, and the league
roster carries each board's public half as the seventh line of its entry
(#100). The hub writes that roster, so it keeps the key per membership. Null
for every SR-game membership, and for a board that has not sent one yet.

Revision ID: f1c6b2a8d407
Revises: e7a2c4d9b153
Create Date: 2026-10-27 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f1c6b2a8d407"
down_revision = "e7a2c4d9b153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("league_memberships", sa.Column("board_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("league_memberships") as batch:
        batch.drop_column("board_key")
