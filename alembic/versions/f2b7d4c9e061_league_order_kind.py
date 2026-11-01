"""league_order_kind

game_resets now holds every order the console gives an IB league's game, not
only resets: a rules change without a reset, a freeze and a thaw. `kind` says
which. Every row until now was a reset.

Revision ID: f2b7d4c9e061
Revises: c4e8a1f2b6d3
Create Date: 2026-11-01 09:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f2b7d4c9e061"
down_revision = "c4e8a1f2b6d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A plain ADD COLUMN, as d4a9c2e7b815: a batch rebuild could drop the
    # one-draft and one-applying indexes' WHERE clauses.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("game_resets")}
    if "kind" not in columns:
        op.add_column("game_resets", sa.Column("kind", sa.String(10), nullable=False,
                                               server_default="reset"))


def downgrade() -> None:
    op.drop_column("game_resets", "kind")
