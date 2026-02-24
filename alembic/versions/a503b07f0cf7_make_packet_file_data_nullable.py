"""Make Packet.file_data nullable (issue #5: move BLOBs to disk)

Revision ID: a503b07f0cf7
Revises: 712e8404c976
Create Date: 2026-02-23

"""
from alembic import op
import sqlalchemy as sa

revision = "a503b07f0cf7"
down_revision = "712e8404c976"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite requires a full table rebuild to change NOT NULL -> nullable.
    # Alembic batch mode handles this transparently.
    with op.batch_alter_table("packets", schema=None) as batch_op:
        batch_op.alter_column(
            "file_data",
            existing_type=sa.LargeBinary(),
            nullable=True,
        )


def downgrade() -> None:
    # Re-enable NOT NULL.  Rows with NULL file_data will violate this;
    # run migrate_packet_blobs.py in reverse (restore BLOBs) before downgrading.
    with op.batch_alter_table("packets", schema=None) as batch_op:
        batch_op.alter_column(
            "file_data",
            existing_type=sa.LargeBinary(),
            nullable=False,
        )
