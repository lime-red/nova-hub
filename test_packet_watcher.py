#!/usr/bin/env python3
"""
Test the packet watcher by creating a test outbound packet
"""

import time
import shutil
from pathlib import Path
import sqlite3

# Configuration
OUTBOUND_FOLDER = "/home/lime/.dosemu/drive_c/bbs/doors/bre_013/OUTBOUND"
DB_PATH = "/home/lime/nova-data/nova-hub.db"
TEST_PACKET = "555B0201.001"  # League 555, BRE, from 02 to 01, sequence 001

def create_test_packet():
    """Create a minimal test packet file"""
    outbound_path = Path(OUTBOUND_FOLDER)
    outbound_path.mkdir(parents=True, exist_ok=True)

    test_file = outbound_path / TEST_PACKET

    # Create a simple test packet with some content
    # Real packets are ZIP files, but for testing the watcher we just need a file
    test_content = b"TEST PACKET DATA FOR WATCHER VERIFICATION"

    print(f"Creating test packet: {test_file}")
    test_file.write_bytes(test_content)
    print(f"✓ Created {TEST_PACKET} ({len(test_content)} bytes)")

    return test_file

def check_database():
    """Check if the packet was registered in the database"""
    print(f"\nChecking database for {TEST_PACKET}...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT filename, league_id, game_type, source_bbs_index, dest_bbs_index,
               sequence, file_size, created_at
        FROM packets
        WHERE filename = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (TEST_PACKET,))

    result = cursor.fetchone()
    conn.close()

    if result:
        print("✓ Packet found in database!")
        print(f"  Filename: {result[0]}")
        print(f"  League ID: {result[1]}")
        print(f"  Game Type: {result[2]}")
        print(f"  Source BBS: {result[3]}")
        print(f"  Dest BBS: {result[4]}")
        print(f"  Sequence: {result[5]}")
        print(f"  File Size: {result[6]} bytes")
        print(f"  Created At: {result[7]}")
        return True
    else:
        print("✗ Packet NOT found in database")
        return False

def check_moved_location():
    """Check if packet was moved to the outbound directory"""
    data_outbound = Path("/home/lime/nova-data/packets/outbound") / TEST_PACKET

    print(f"\nChecking if packet was moved to: {data_outbound}")

    if data_outbound.exists():
        size = data_outbound.stat().st_size
        print(f"✓ Packet found in outbound directory ({size} bytes)")
        return True
    else:
        print("✗ Packet NOT found in outbound directory")
        return False

def main():
    print("=" * 70)
    print("PACKET WATCHER TEST")
    print("=" * 70)

    print("\nThis test will:")
    print("1. Create a test packet in the DOSEMU outbound folder")
    print("2. Wait for the watcher to detect and process it (via inotify)")
    print("3. Verify the packet was registered in the database")
    print("4. Verify the packet was moved to the hub's outbound directory")
    print("\nNOTE: If you restart the server with existing packets in the folder,")
    print("      they will be processed automatically at startup!")

    print("\n" + "=" * 70)
    input("Press ENTER to start the test (make sure the server is running)...")

    # Step 1: Create test packet
    test_file = create_test_packet()

    # Step 2: Wait for watcher to process
    print("\nWaiting 5 seconds for watcher to detect and process the packet...")
    for i in range(5, 0, -1):
        print(f"  {i}...", end="\r")
        time.sleep(1)
    print()

    # Step 3 & 4: Check results
    db_success = check_database()
    move_success = check_moved_location()

    # Summary
    print("\n" + "=" * 70)
    print("TEST RESULTS")
    print("=" * 70)

    if db_success and move_success:
        print("✓ ALL TESTS PASSED - Packet watcher is working correctly!")
        print("\nThe watcher successfully:")
        print("  1. Detected the new packet file")
        print("  2. Registered it in the database")
        print("  3. Moved it to the hub's outbound directory")
    else:
        print("✗ TEST FAILED - Packet watcher did not process the packet")
        print("\nPossible issues:")
        if not db_success:
            print("  - Packet not registered in database (watcher may not be running)")
        if not move_success:
            print("  - Packet not moved (file operation failed)")
        print("\nCheck the server logs for errors")
        print("Verify the watcher started: look for '[Watcher] Started monitoring...' in logs")

    # Cleanup
    if test_file.exists():
        print(f"\nNote: Original test file still exists at: {test_file}")
        print("      (This may indicate the watcher didn't pick it up)")

    print("=" * 70)

if __name__ == "__main__":
    main()
