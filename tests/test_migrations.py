"""The migrations and the models must describe the same database.

Production runs `alembic upgrade head`. Every other test in this suite runs
`Base.metadata.create_all`. Nothing compared the two, so a model could gain a
column, the tests could stay green, and production could run without it --
which is exactly the shape of the gap that let three migrations pile up
undeployed while prod kept working.

These tests migrate a real (temporary) SQLite file and compare the result to the
models. They are not live-rig tests: no dosemu, no games, no network.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from backend.models.database import Base

REPO = Path(__file__).resolve().parent.parent


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "alembic"))
    # env.py reads this first; see the precedence note there.
    os.environ["NOVA_HUB_DB_URL"] = f"sqlite:///{db_path}"
    return cfg


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """A file-backed SQLite database at the migration head."""
    db_path = tmp_path / "migrated.db"
    monkeypatch.setenv("NOVA_HUB_DB_URL", f"sqlite:///{db_path}")
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    return db_path


class TestMigrationChain:
    def test_there_is_exactly_one_head(self):
        """Two heads mean someone branched the chain and alembic will refuse.

        Worth catching in CI rather than on the production box mid-deploy.
        """
        script = ScriptDirectory(str(REPO / "alembic"))
        heads = script.get_heads()
        assert len(heads) == 1, f"expected a single head, found {heads}"

    def test_every_revision_is_reachable_from_the_head(self):
        """No orphans: walking back from head must visit every revision file."""
        script = ScriptDirectory(str(REPO / "alembic"))
        head = script.get_current_head()
        walked = {rev.revision for rev in script.walk_revisions("base", head)}
        on_disk = {rev.revision for rev in script.get_revisions("heads")}
        for rev in script.walk_revisions():
            on_disk.add(rev.revision)
        assert on_disk - walked == set(), (
            f"revisions not reachable from head {head}: {on_disk - walked}"
        )


class TestSchemaMatchesModels:
    def test_upgrade_head_produces_the_model_schema(self, migrated_db):
        """The drift check.

        Migrate an empty database to head, then ask alembic to autogenerate
        against the models. A non-empty diff means the models and the migrations
        disagree -- someone changed a model without writing a migration, and
        production would be missing the column.
        """
        engine = create_engine(f"sqlite:///{migrated_db}")
        try:
            with engine.connect() as conn:
                ctx = MigrationContext.configure(
                    conn,
                    opts={
                        # SQLite cannot reflect these faithfully, and alembic
                        # reports them as spurious diffs on every run.
                        "compare_type": False,
                        "compare_server_default": False,
                    },
                )
                diff = compare_metadata(ctx, Base.metadata)
        finally:
            engine.dispose()

        # Ignore alembic's own bookkeeping table.
        diff = [d for d in diff if "alembic_version" not in repr(d)]

        assert diff == [], (
            "the migrations and the models disagree:\n  "
            + "\n  ".join(repr(d) for d in diff)
            + "\n\nIf a model changed, add a migration:\n"
            "  .venv/bin/alembic revision --autogenerate -m 'describe it'"
        )

    def test_create_all_and_upgrade_head_agree_on_tables(self, migrated_db):
        """The two ways this project builds a database must produce the same tables.

        Unit tests use create_all; production uses alembic. If these diverge the
        test suite is exercising a schema that does not exist anywhere else.
        """
        migrated = create_engine(f"sqlite:///{migrated_db}")
        fresh = create_engine("sqlite:///:memory:")
        try:
            Base.metadata.create_all(fresh)
            migrated_tables = set(inspect(migrated).get_table_names()) - {"alembic_version"}
            model_tables = set(inspect(fresh).get_table_names())
            assert migrated_tables == model_tables, (
                f"only in migrations: {sorted(migrated_tables - model_tables)}; "
                f"only in models: {sorted(model_tables - migrated_tables)}"
            )
        finally:
            migrated.dispose()
            fresh.dispose()

    @pytest.mark.parametrize(
        "table,column",
        [
            ("processing_runs", "packets_unconsumed"),
            ("processing_run_files", "league_id"),
            ("processing_runs", "exit_code"),
        ],
    )
    def test_columns_the_deploy_depends_on_exist(self, migrated_db, table, column):
        """Named explicitly, because these three are what production was missing.

        A generic drift check passes the moment models and migrations agree with
        each other -- even if both are wrong. These name the columns that carry
        the R-1 and R-3 signals.
        """
        engine = create_engine(f"sqlite:///{migrated_db}")
        try:
            names = [c["name"] for c in inspect(engine).get_columns(table)]
            assert column in names, f"{table}.{column} missing; have {names}"
        finally:
            engine.dispose()


class TestMigrationsAreIdempotent:
    def test_upgrade_head_twice_is_a_no_op(self, migrated_db):
        """Re-running the deploy step must not fail.

        The migrations guard themselves with _column_exists checks precisely so a
        half-finished deploy can be retried. That guard is only useful if it works.
        """
        cfg = _alembic_config(migrated_db)
        command.upgrade(cfg, "head")  # would raise if not idempotent

    def test_downgrade_then_upgrade_round_trips(self, migrated_db):
        """Rollback has to actually work; it is the documented recovery path."""
        cfg = _alembic_config(migrated_db)
        engine = create_engine(f"sqlite:///{migrated_db}")
        try:
            before = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()

        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{migrated_db}")
        try:
            after = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert before == after


class TestFtnAddressesMoveOntoTheBbs:
    """a7d3e9f15b20 moves free-text membership addresses onto the BBS.

    Run against data, because the empty-database checks above cannot see the
    part that matters: the copy, the link-up, and the refusal on a clash.
    """

    BEFORE = "c5f2a8d61e04"

    def _db_at_before(self, tmp_path, monkeypatch, memberships):
        import sqlite3

        db_path = tmp_path / "ftn.db"
        monkeypatch.setenv("NOVA_HUB_DB_URL", f"sqlite:///{db_path}")
        cfg = _alembic_config(db_path)
        command.upgrade(cfg, self.BEFORE)
        con = sqlite3.connect(db_path)
        con.executemany(
            "INSERT INTO clients (id, client_id, client_secret, bbs_name) VALUES (?, ?, 'x', ?)",
            [(1, "sj", "Starship Junkyard"), (2, "ecl", "The Eclipse")],
        )
        con.executemany(
            "INSERT INTO leagues (id, league_id, game_type, name) VALUES (?, ?, ?, ?)",
            [(1, "014", "B", "014B"), (2, "015", "B", "015B"), (3, "015", "F", "015F")],
        )
        con.executemany(
            "INSERT INTO league_memberships (client_id, league_id, bbs_index, fidonet_address)"
            " VALUES (?, ?, ?, ?)",
            memberships,
        )
        con.commit()
        con.close()
        return db_path, cfg

    def test_addresses_are_copied_once_per_bbs_and_linked(self, tmp_path, monkeypatch):
        import sqlite3

        db_path, cfg = self._db_at_before(tmp_path, monkeypatch, [
            (1, 1, 2, "135:135/20"),
            (1, 2, 2, " 135:135/8 "),   # stray whitespace is trimmed
            (2, 2, 4, "135:135/21"),
            (2, 3, 4, "135:135/21"),   # same BBS, same address, two leagues: one row
        ])

        command.upgrade(cfg, "head")

        con = sqlite3.connect(db_path)
        try:
            addresses = con.execute(
                "SELECT client_id, address FROM ftn_addresses ORDER BY client_id, address"
            ).fetchall()
            links = con.execute(
                "SELECT m.client_id, m.league_id, f.address FROM league_memberships m"
                " JOIN ftn_addresses f ON f.id = m.ftn_address_id"
                " WHERE f.client_id = m.client_id ORDER BY m.client_id, m.league_id"
            ).fetchall()
            columns = [r[1] for r in con.execute("PRAGMA table_info(league_memberships)")]
        finally:
            con.close()
        assert addresses == [(1, "135:135/20"), (1, "135:135/8"), (2, "135:135/21")]
        assert links == [(1, 1, "135:135/20"), (1, 2, "135:135/8"),
                         (2, 2, "135:135/21"), (2, 3, "135:135/21")]
        assert "fidonet_address" not in columns

    def test_an_address_held_by_two_bbses_refuses_and_changes_nothing(self, tmp_path, monkeypatch):
        """Production's real clash: /20 was Starship Junkyard in 014 and The Eclipse in 015."""
        import sqlite3

        db_path, cfg = self._db_at_before(tmp_path, monkeypatch, [
            (1, 1, 2, "135:135/20"),
            (2, 2, 4, "135:135/20"),
        ])

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(cfg, "head")

        message = str(excinfo.value)
        assert "135:135/20 by Starship Junkyard in 014B" in message
        assert "135:135/20 by The Eclipse in 015B" in message
        con = sqlite3.connect(db_path)
        try:
            version = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            kept = con.execute("SELECT COUNT(*) FROM league_memberships"
                               " WHERE fidonet_address = '135:135/20'").fetchone()[0]
        finally:
            con.close()
        assert version == self.BEFORE
        assert "ftn_addresses" not in tables
        assert kept == 2

    def test_downgrade_puts_the_text_back(self, tmp_path, monkeypatch):
        import sqlite3

        db_path, cfg = self._db_at_before(tmp_path, monkeypatch, [
            (1, 1, 2, "135:135/20"),
            (2, 2, 4, "135:135/21"),
        ])
        command.upgrade(cfg, "head")

        command.downgrade(cfg, self.BEFORE)

        con = sqlite3.connect(db_path)
        try:
            rows = con.execute("SELECT client_id, fidonet_address FROM league_memberships"
                               " ORDER BY client_id").fetchall()
        finally:
            con.close()
        assert rows == [(1, "135:135/20"), (2, "135:135/21")]
