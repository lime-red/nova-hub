"""add_nodelist_identity_fields

The generated nodelist was missing everything the database could not express:
the hub's own node entry (it is not a Client row) and the city/state/country
lines every hand-made nodes.dat carries. Both gaps are filled here.

- clients.city/state/country: properties of the BBS, identical across the
  leagues it belongs to, so they sit beside contact_name rather than on the
  membership.
- leagues.hub_fidonet_address: the hub's address *within this league*. It is
  per-league (013 uses 13:10/1, 015 uses 135:1/1), which is why it cannot live
  in [hub] config alongside the name and index.
- leagues.hub_routes_mail: whether the hub's entry carries the game's
  "<index> HOST <targets...>" routing directive. True everywhere in production,
  but 013's nodes.dat has a bare "1", and generating a HOST line over the top
  of that would change how that league routes. Defaults to 1 because that is
  what the live leagues do; the backfill sets it from what each file says.

Additive and nullable. Existing rows read as NULL, and NodelistGenerator
refuses to write a nodelist while hub_fidonet_address is unset rather than
emitting a file with no hub in it.

Revision ID: e4a1c9b27d30
Revises: d7c2f19a4b83
Create Date: 2026-09-14 10:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "e4a1c9b27d30"
down_revision = "d7c2f19a4b83"
branch_labels = None
depends_on = None


_CLIENT_COLUMNS = ("city", "state", "country")
_LEAGUE_COLUMNS = ("hub_routes_mail", "hub_fidonet_address")


def upgrade() -> None:
    # Checks for existence first for idempotency (SQLite compatible).
    for column in _CLIENT_COLUMNS:
        if not _column_exists("clients", column):
            op.execute(f"ALTER TABLE clients ADD COLUMN {column} VARCHAR(50)")

    if not _column_exists("leagues", "hub_fidonet_address"):
        op.execute("ALTER TABLE leagues ADD COLUMN hub_fidonet_address VARCHAR(50)")

    if not _column_exists("leagues", "hub_routes_mail"):
        op.execute("ALTER TABLE leagues ADD COLUMN hub_routes_mail BOOLEAN DEFAULT 1")


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; use batch mode.
    # Guarded the same way as upgrade(): a database stamped at this revision
    # before a column was added to it must still be able to come back down.
    with op.batch_alter_table("leagues") as batch_op:
        for column in _LEAGUE_COLUMNS:
            if _column_exists("leagues", column):
                batch_op.drop_column(column)
    with op.batch_alter_table("clients") as batch_op:
        for column in _CLIENT_COLUMNS:
            if _column_exists("clients", column):
                batch_op.drop_column(column)


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))
