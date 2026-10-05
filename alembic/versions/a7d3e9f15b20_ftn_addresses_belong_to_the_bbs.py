"""ftn_addresses_belong_to_the_bbs

A FidoNet address was free text on each league membership, unique only within
its league. Allocated by hand, that let one address go to two BBSes in two
leagues (135:135/20 was Starship Junkyard in 014 and The Eclipse in 015) and one
BBS collect two addresses in the same net, with nothing to notice either.

The address now belongs to the BBS: a new ftn_addresses table, unique across the
hub, owned by a client. A membership points at one of its own BBS's addresses
through league_memberships.ftn_address_id, and the free-text column goes.

Upgrade refuses -- before touching anything -- if any address is held by more
than one BBS, and names them. That clash has to be resolved by an admin in the
console (point one of the memberships at a different address) and the upgrade
re-run; the migration will not guess which BBS should keep it.

Revision ID: a7d3e9f15b20
Revises: c5f2a8d61e04
Create Date: 2026-10-05 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7d3e9f15b20"
down_revision = "c5f2a8d61e04"
branch_labels = None
depends_on = None


FK_NAME = "fk_league_memberships_ftn_address_id"


def upgrade() -> None:
    bind = op.get_bind()

    if _column_exists("league_memberships", "fidonet_address"):
        _refuse_on_clashes(bind)

    if not _table_exists("ftn_addresses"):
        op.create_table(
            "ftn_addresses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("client_id", sa.Integer(), nullable=False),
            sa.Column("address", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ftn_addresses_id", "ftn_addresses", ["id"])
        op.create_index("ix_ftn_addresses_client_id", "ftn_addresses", ["client_id"])
        op.create_index("ix_ftn_addresses_address", "ftn_addresses", ["address"], unique=True)

    if not _column_exists("league_memberships", "ftn_address_id"):
        with op.batch_alter_table("league_memberships") as batch_op:
            batch_op.add_column(sa.Column("ftn_address_id", sa.Integer(), nullable=True))
            batch_op.create_index("ix_league_memberships_ftn_address_id", ["ftn_address_id"])
            batch_op.create_foreign_key(FK_NAME, "ftn_addresses", ["ftn_address_id"], ["id"])

    if _column_exists("league_memberships", "fidonet_address"):
        bind.execute(sa.text(
            """
            INSERT INTO ftn_addresses (client_id, address, created_at)
            SELECT DISTINCT client_id, TRIM(fidonet_address), CURRENT_TIMESTAMP
            FROM league_memberships
            WHERE fidonet_address IS NOT NULL AND TRIM(fidonet_address) != ''
              AND TRIM(fidonet_address) NOT IN (SELECT address FROM ftn_addresses)
            """
        ))
        bind.execute(sa.text(
            """
            UPDATE league_memberships
            SET ftn_address_id = (
                SELECT f.id FROM ftn_addresses f
                WHERE f.address = TRIM(league_memberships.fidonet_address)
            )
            WHERE fidonet_address IS NOT NULL AND TRIM(fidonet_address) != ''
            """
        ))
        if _index_exists("league_memberships", "ix_league_memberships_fidonet_address"):
            op.drop_index("ix_league_memberships_fidonet_address", table_name="league_memberships")
        with op.batch_alter_table("league_memberships") as batch_op:
            batch_op.drop_column("fidonet_address")


def downgrade() -> None:
    bind = op.get_bind()

    if not _column_exists("league_memberships", "fidonet_address"):
        with op.batch_alter_table("league_memberships") as batch_op:
            batch_op.add_column(sa.Column("fidonet_address", sa.String(length=50), nullable=True))
        op.create_index(
            "ix_league_memberships_fidonet_address", "league_memberships", ["fidonet_address"]
        )

    if _column_exists("league_memberships", "ftn_address_id"):
        bind.execute(sa.text(
            """
            UPDATE league_memberships
            SET fidonet_address = (
                SELECT f.address FROM ftn_addresses f
                WHERE f.id = league_memberships.ftn_address_id
            )
            WHERE ftn_address_id IS NOT NULL
            """
        ))
        with op.batch_alter_table("league_memberships") as batch_op:
            batch_op.drop_constraint(FK_NAME, type_="foreignkey")
            batch_op.drop_index("ix_league_memberships_ftn_address_id")
            batch_op.drop_column("ftn_address_id")

    # An address with no membership (added on the BBS but never used) has
    # nowhere to go in the old schema and is lost here.
    if _table_exists("ftn_addresses"):
        op.drop_index("ix_ftn_addresses_address", table_name="ftn_addresses")
        op.drop_index("ix_ftn_addresses_client_id", table_name="ftn_addresses")
        op.drop_index("ix_ftn_addresses_id", table_name="ftn_addresses")
        op.drop_table("ftn_addresses")


def _refuse_on_clashes(bind) -> None:
    rows = bind.execute(sa.text(
        """
        SELECT TRIM(m.fidonet_address) AS address,
               c.bbs_name, l.league_id || l.game_type AS league
        FROM league_memberships m
        JOIN clients c ON c.id = m.client_id
        JOIN leagues l ON l.id = m.league_id
        WHERE TRIM(m.fidonet_address) IN (
            SELECT TRIM(fidonet_address) FROM league_memberships
            WHERE fidonet_address IS NOT NULL AND TRIM(fidonet_address) != ''
            GROUP BY TRIM(fidonet_address)
            HAVING COUNT(DISTINCT client_id) > 1
        )
        ORDER BY address, c.bbs_name, league
        """
    )).fetchall()
    if rows:
        held = "; ".join(f"{r.address} by {r.bbs_name} in {r.league}" for r in rows)
        raise RuntimeError(
            "Cannot move FTN addresses onto BBSes: an address is held by more than "
            f"one BBS ({held}). Give one of them a different address in the console, "
            "then re-run the upgrade. Nothing has been changed."
        )


def _table_exists(table: str) -> bool:
    from sqlalchemy import inspect
    return table in inspect(op.get_bind()).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    from sqlalchemy import inspect
    return any(c["name"] == column for c in inspect(op.get_bind()).get_columns(table))


def _index_exists(table: str, name: str) -> bool:
    from sqlalchemy import inspect
    return any(i["name"] == name for i in inspect(op.get_bind()).get_indexes(table))
