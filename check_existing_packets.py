#!/usr/bin/env python3
"""
Quick script to check for existing packets in the OUTBOUND folder
Run this to see what would be picked up on server restart
"""

from pathlib import Path
import re

OUTBOUND_FOLDER = "/home/lime/.dosemu/drive_c/bbs/doors/bre_013/OUTBOUND"
PACKET_REGEX = re.compile(r'^(\d{3})([BF])([0-9A-F]{2})([0-9A-F]{2})\.(\d{3})$', re.IGNORECASE)

def check_existing():
    """Check what packet files exist in the outbound folder"""
    outbound_path = Path(OUTBOUND_FOLDER)

    if not outbound_path.exists():
        print(f"✗ Outbound folder does not exist: {OUTBOUND_FOLDER}")
        return

    files = [f for f in outbound_path.glob("*") if f.is_file()]

    if not files:
        print(f"✓ Outbound folder exists but is empty")
        print(f"  Location: {OUTBOUND_FOLDER}")
        return

    print(f"\nFound {len(files)} file(s) in outbound folder:")
    print(f"Location: {OUTBOUND_FOLDER}\n")

    packet_count = 0
    other_count = 0

    for file in sorted(files):
        match = PACKET_REGEX.match(file.name)
        if match:
            packet_count += 1
            size = file.stat().st_size
            league, game, src, dst, seq = match.groups()
            print(f"  ✓ {file.name:20s} - League {league}, {game}, {src}→{dst}, seq {seq} ({size:,} bytes)")
        else:
            other_count += 1
            print(f"  ✗ {file.name:20s} - NOT a valid packet filename")

    print(f"\nSummary:")
    print(f"  Valid packets: {packet_count}")
    print(f"  Other files:   {other_count}")

    if packet_count > 0:
        print(f"\n✓ These {packet_count} packet(s) will be automatically processed when the server starts!")
        print(f"  They will be:")
        print(f"    1. Registered in the database")
        print(f"    2. Moved to /home/lime/nova-data/packets/outbound/")
        print(f"    3. Made available for download via the API")
    else:
        print(f"\n  No valid packets found to process.")

if __name__ == "__main__":
    print("=" * 70)
    print("EXISTING PACKETS CHECK")
    print("=" * 70)
    check_existing()
    print("=" * 70)
