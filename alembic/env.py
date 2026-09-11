# alembic/env.py
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import from new backend location
from backend.models.database import Base

# this is the Alembic Config object
config = context.config

# Where to migrate. Precedence: NOVA_HUB_DB_URL, then config.toml, then
# alembic.ini.
#
# The env var exists because config.toml is resolved relative to this file, so
# without it the only way to point alembic at a different database is to copy the
# whole tree and edit the copy's config.toml. That is what a production migration
# rehearsal needs, and what the migration tests need, and it should not require
# a tree copy.
import os as _os

_url_override = _os.environ.get("NOVA_HUB_DB_URL")
if _url_override:
    config.set_main_option("sqlalchemy.url", _url_override)
else:
    try:
        import toml as _toml
        _app_config = _toml.load(Path(__file__).resolve().parent.parent / "config.toml")
        _db_path = _app_config.get("database", {}).get("path", "./data/nova-hub.db")
        config.set_main_option("sqlalchemy.url", f"sqlite:///{_db_path}")
    except FileNotFoundError:
        pass  # Fall back to alembic.ini value

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
