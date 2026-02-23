#!/usr/bin/env python3
"""
migrate_packet_blobs.py - One-time data migration for issue #5

Extracts packet binary data that was previously stored as BLOBs in the
Packet.file_data database column, writes the data to disk if the file is not
already present, and then NULLs out the file_data column to free space.

Run AFTER `alembic upgrade head` (which makes file_data nullable):

    uv run python migrate_packet_blobs.py [--data-dir /path/to/data] [--dry-run]

The script is idempotent: re-running it after it completes will find no
packets with non-null file_data and exit cleanly.
"""

import argparse
import sys
from pathlib import Path

import toml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent))

from backend.models.database import League, Packet


def recover_to_disk(packet: Packet, data_dir: Path, league: League | None, dry_run: bool) -> str:
    """
    Write packet.file_data to the most appropriate location on disk.
    Returns a string describing what was done.
    """
    filename = packet.filename
    is_nodelist = filename.upper().startswith(("BRNODES.", "FENODES."))

    # Determine candidate paths
    candidates = [
        data_dir / "packets" / "outbound" / filename,
        data_dir / "packets" / "inbound" / filename,
        data_dir / "packets" / "processed" / filename,
    ]

    if is_nodelist and league:
        game_type_str = "bre" if league.game_type == "B" else "fe"
        candidates.insert(
            0,
            data_dir / "nodelists" / game_type_str / league.league_id / filename,
        )

    # Check if file already exists anywhere
    for candidate in candidates:
        if candidate.exists():
            return f"  already on disk: {candidate}"

    # Not on disk — write to recovery directory
    recovery_dir = data_dir / "packets" / "recovered"
    if not dry_run:
        recovery_dir.mkdir(parents=True, exist_ok=True)
        recovery_path = recovery_dir / filename
        recovery_path.write_bytes(packet.file_data)
    return f"  RECOVERED to {recovery_dir / filename}"


def main():
    parser = argparse.ArgumentParser(description="Migrate Packet.file_data BLOBs to disk")
    parser.add_argument("--config", default="config.toml", help="Path to config.toml")
    parser.add_argument("--data-dir", default=None, help="Override data_dir from config")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without changing anything")
    args = parser.parse_args()

    # Load config
    config = toml.load(args.config)
    data_dir = Path(args.data_dir or config.get("server", {}).get("data_dir", "./data"))
    db_path = config.get("database", {}).get("path", "./data/nova-hub.db")

    print(f"Data directory : {data_dir}")
    print(f"Database       : {db_path}")
    print(f"Dry run        : {args.dry_run}")
    print()

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Count packets with non-null file_data
        total = db.query(Packet).filter(Packet.file_data.isnot(None)).count()
        print(f"Packets with BLOB data: {total}")

        if total == 0:
            print("Nothing to migrate. Exiting.")
            return

        already_on_disk = 0
        recovered = 0
        nulled = 0

        packets = db.query(Packet).filter(Packet.file_data.isnot(None)).all()
        for packet in packets:
            league = db.query(League).filter(League.id == packet.league_id).first()
            msg = recover_to_disk(packet, data_dir, league, args.dry_run)
            print(f"Packet {packet.filename}: {msg}")

            if "already on disk" in msg:
                already_on_disk += 1
            else:
                recovered += 1

            if not args.dry_run:
                packet.file_data = None
                nulled += 1

        if not args.dry_run:
            db.commit()
            print(f"\nDone. {already_on_disk} already on disk, {recovered} recovered, {nulled} BLOBs nulled.")
        else:
            print(f"\nDry run complete. {already_on_disk} already on disk, {recovered} would be recovered.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
