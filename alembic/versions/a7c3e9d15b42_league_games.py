"""league_games

Where each league's game is moves out of config.toml and into the database:
league_games, a row per league whose game the hub runs, and game_images, the
installs those games are made from. processing_runs gains packets_quarantined,
for files a game wrote that were not its own league's.

The rows themselves come from migrate_league_games.py, which also moves the
game folders; this only makes the tables.

Revision ID: a7c3e9d15b42
Revises: f2b7d4c9e061
Create Date: 2026-11-02 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7c3e9d15b42"
down_revision = "f2b7d4c9e061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_type", sa.String(1), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("dirname", sa.String(100), nullable=False, unique=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_game_images_id", "game_images", ["id"])
    op.create_table(
        "league_games",
        sa.Column("league_id", sa.Integer(), sa.ForeignKey("leagues.id"), primary_key=True),
        sa.Column("folder", sa.String(8), nullable=False, unique=True),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("game_images.id"), nullable=True),
        sa.Column("hub_board_key", sa.String(64), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("preflight_ok", sa.Boolean(), nullable=True),
        sa.Column("preflight_at", sa.DateTime(), nullable=True),
        sa.Column("preflight_problems", sa.JSON(), nullable=True),
    )
    # A plain ADD COLUMN, as f2b7d4c9e061.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("processing_runs")}
    if "packets_quarantined" not in columns:
        op.add_column("processing_runs", sa.Column("packets_quarantined", sa.Integer(),
                                                   nullable=True, server_default="0"))


def downgrade() -> None:
    op.drop_column("processing_runs", "packets_quarantined")
    op.drop_table("league_games")
    op.drop_index("ix_game_images_id", "game_images")
    op.drop_table("game_images")
