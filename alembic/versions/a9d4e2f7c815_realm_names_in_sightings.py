"""realm_names_in_sightings

Sightings name the realms involved, as the hub knew them when it read the
packet: a BRE realm letter is reused after a reset or a deleted realm, so the
name is copied into the sighting rather than looked up later. realm_names holds
the latest name per BRE realm slot, from each planet's Recon Update.

Immortal Barons realms have names and no letters, so the letters become
nullable, and an IB attack's ID ("<game start>.<origin node>.<id>") is longer
than BRE's 16 hex digits.

Revision ID: a9d4e2f7c815
Revises: f1c6b2a8d407
Create Date: 2026-10-31 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a9d4e2f7c815"
down_revision = "f1c6b2a8d407"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attack_sightings") as batch:
        batch.alter_column("attack_id", existing_type=sa.String(length=16),
                           type_=sa.String(length=40), existing_nullable=False)
        batch.alter_column("attacker", existing_type=sa.String(length=1), nullable=True)
        batch.alter_column("target", existing_type=sa.String(length=1), nullable=True)
        batch.add_column(sa.Column("attacker_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("target_name", sa.String(length=64), nullable=True))
    with op.batch_alter_table("traffic_sightings") as batch:
        batch.add_column(sa.Column("from_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("to_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("recipient_names", sa.JSON(), nullable=True))
    op.create_table(
        "realm_names",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("planet", sa.Integer(), nullable=False),
        sa.Column("letter", sa.String(length=1), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("packet_id", sa.Integer(), nullable=True),
        sa.Column("seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"]),
        sa.ForeignKeyConstraint(["packet_id"], ["packets.id"]),
        sa.PrimaryKeyConstraint("league_id", "planet", "letter"),
    )


def downgrade() -> None:
    op.drop_table("realm_names")
    with op.batch_alter_table("traffic_sightings") as batch:
        batch.drop_column("recipient_names")
        batch.drop_column("to_name")
        batch.drop_column("from_name")
    # Sightings without a letter (IB's) cannot go back into the older table.
    op.execute("DELETE FROM attack_sightings WHERE attacker IS NULL OR target IS NULL "
               "OR length(attack_id) > 16")
    with op.batch_alter_table("attack_sightings") as batch:
        batch.drop_column("target_name")
        batch.drop_column("attacker_name")
        batch.alter_column("target", existing_type=sa.String(length=1), nullable=False)
        batch.alter_column("attacker", existing_type=sa.String(length=1), nullable=False)
        batch.alter_column("attack_id", existing_type=sa.String(length=40),
                           type_=sa.String(length=16), existing_nullable=False)
