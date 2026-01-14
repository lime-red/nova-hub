#!/usr/bin/env python3
"""
Database migration: Add ProcessingRunFile table and error_message column

This script:
1. Adds the processing_run_files table to store score files, routes files, and bbsinfo files
2. Adds error_message column to processing_runs table

Usage:
    python migrate_add_processing_files.py
"""

import sqlite3
import toml
from pathlib import Path
from app.database import Base, init_database, ProcessingRunFile, ProcessingRun

def migrate():
    """Add ProcessingRunFile table and error_message column to database"""

    # Load config to find database path
    config = toml.load("config.toml")
    db_path = Path(config["database"]["path"])

    print("=" * 60)
    print("Nova Hub Database Migration")
    print("Add ProcessingRunFile table and error_message column")
    print("=" * 60)
    print()
    print(f"Database: {db_path}")
    print()

    if not db_path.exists():
        print(f"ERROR: Database file does not exist: {db_path}")
        print("Please run the server first to create the initial database.")
        return False

    print("Connecting to database...")

    # Use raw SQLite connection for ALTER TABLE
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Check if error_message column exists
    cursor.execute("PRAGMA table_info(processing_runs)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'error_message' not in columns:
        print("Adding error_message column to processing_runs table...")
        cursor.execute("ALTER TABLE processing_runs ADD COLUMN error_message TEXT")
        conn.commit()
        print("✓ error_message column added")
    else:
        print("✓ error_message column already exists")

    conn.close()

    # Now use SQLAlchemy to create new tables
    print("Creating new tables...")
    database_url = f"sqlite:///{db_path}"
    engine = init_database(database_url)

    # This will create only tables that don't exist yet
    Base.metadata.create_all(bind=engine)

    print("✓ Migration complete!")
    print()
    print("Changes applied:")
    print("- Added error_message column to processing_runs table")
    print("- Created processing_run_files table")
    print()
    print("You can now restart the server to use the new features.")
    print()

    return True

if __name__ == "__main__":
    migrate()
