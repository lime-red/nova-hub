#!/usr/bin/env python3
"""
Database reset script for Nova Hub

This script will:
1. Stop the server (you must do this manually first)
2. Delete the database file
3. Recreate all tables from scratch
4. Optionally preserve league and client configuration

Usage:
    python reset_database.py [--preserve-config]

Options:
    --preserve-config    Keep league and client/membership data, only clear packets
"""

import argparse
import sys
from pathlib import Path

import toml


def reset_database(preserve_config=False):
    """Reset the database"""

    # Load config to find database path
    config = toml.load("config.toml")
    db_path = Path(config["database"]["path"])

    print("=" * 60)
    print("Nova Hub Database Reset")
    print("=" * 60)
    print()
    print(f"Database: {db_path}")
    print(f"Mode: {'Preserve config' if preserve_config else 'Full reset'}")
    print()

    if not db_path.exists():
        print(f"Database file does not exist: {db_path}")
        print("Nothing to reset.")
        return

    print("WARNING: This will delete all data!")
    if preserve_config:
        print("- Leagues and clients will be preserved")
        print("- All packets, processing runs, and sequence alerts will be deleted")
    else:
        print("- ALL data will be deleted (leagues, clients, packets, everything)")
    print()

    response = input("Are you sure you want to continue? [yes/NO]: ")
    if response.lower() != 'yes':
        print("Reset cancelled.")
        return

    if preserve_config:
        # Preserve config mode - delete specific tables
        print("\nPreserving configuration mode not yet implemented.")
        print("Please use full reset for now.")
        return
    else:
        # Full reset - delete database file
        print(f"\nDeleting database: {db_path}")
        db_path.unlink()
        print("✓ Database deleted")

    # Recreate database
    print("\nRecreating database tables...")
    from backend.models.database import Base
    from backend.core.database import init_database

    # Initialize database connection
    database_url = f"sqlite:///{db_path}"
    engine = init_database(database_url)

    # Create all tables
    Base.metadata.create_all(bind=engine)
    print("✓ Database tables created")

    print("\n" + "=" * 60)
    print("Database reset complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Start the server")
    print("2. Use the admin panel to recreate leagues and clients")
    print("3. Clients will need to re-register and join leagues")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset Nova Hub database")
    parser.add_argument(
        "--preserve-config",
        action="store_true",
        help="Keep league and client configuration"
    )

    args = parser.parse_args()

    try:
        reset_database(preserve_config=args.preserve_config)
    except KeyboardInterrupt:
        print("\n\nReset cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
