"""sysop_accounts

Sysops sign in through an identity provider: sysop_users gains idp_subject (the
provider's ID for the person) and hashed_password becomes optional, since a
provider-only account has none. client_owners says which sysops run which BBS;
audit_events records who changed what.

Revision ID: 6d2f8a41c9e7
Revises: b81c4f2d9a63
Create Date: 2026-10-10 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "6d2f8a41c9e7"
down_revision = "b81c4f2d9a63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("sysop_users")}
    if "idp_subject" not in columns:
        # SQLite cannot alter a column in place; batch mode copies the table.
        with op.batch_alter_table("sysop_users") as batch:
            batch.add_column(sa.Column("idp_subject", sa.String(length=64), nullable=True))
            batch.alter_column("hashed_password", existing_type=sa.String(length=255),
                               nullable=True)
            batch.create_index("ix_sysop_users_idp_subject", ["idp_subject"], unique=True)

    if not _table_exists("client_owners"):
        op.create_table(
            "client_owners",
            sa.Column("sysop_user_id", sa.Integer(), nullable=False),
            sa.Column("client_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(length=50), nullable=True),
            sa.ForeignKeyConstraint(["sysop_user_id"], ["sysop_users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("sysop_user_id", "client_id"),
        )
        op.create_index("ix_client_owners_client_id", "client_owners", ["client_id"])

    if not _table_exists("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("actor", sa.String(length=100), nullable=True),
            sa.Column("action", sa.String(length=50), nullable=False),
            sa.Column("target_type", sa.String(length=30), nullable=True),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("target", sa.String(length=100), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_audit_events_id", "audit_events", ["id"])
        op.create_index("ix_audit_events_at", "audit_events", ["at"])
        op.create_index("ix_audit_events_action", "audit_events", ["action"])
        op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"])


def downgrade() -> None:
    if _table_exists("audit_events"):
        op.drop_table("audit_events")
    if _table_exists("client_owners"):
        op.drop_table("client_owners")
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("sysop_users")}
    if "idp_subject" in columns:
        # Refuses (NOT NULL) while provider-only accounts exist: delete or give
        # them passwords first, rather than losing them silently.
        with op.batch_alter_table("sysop_users") as batch:
            batch.drop_index("ix_sysop_users_idp_subject")
            batch.drop_column("idp_subject")
            batch.alter_column("hashed_password", existing_type=sa.String(length=255),
                               nullable=False)


def _table_exists(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()
