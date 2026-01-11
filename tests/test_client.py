# test_client.py - Automated test script

import asyncio
import json
import shutil
from pathlib import Path


async def setup_test_environment():
    """Set up test directories and files"""
    print("Setting up test environment...")

    # Create test directories
    test_dirs = [
        "test_data/bre_555/outbound",
        "test_data/bre_555/inbound",
        "test_data/fe_123/outbound",
        "test_data/fe_123/inbound",
        "sent",
    ]

    for dir_path in test_dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    # Create test outbound packets
    # BRE packet from BBS 02 to BBS 01 (hub)
    bre_packet = Path("test_data/bre_555/outbound/555B0201.001")
    bre_packet.write_bytes(b"Test BRE packet from BBS 02 to hub\n")

    # FE packet from BBS 02 to BBS 01 (hub)
    fe_packet = Path("test_data/fe_123/outbound/123F0201.001")
    fe_packet.write_bytes(b"Test FE packet from BBS 02 to hub\n")

    print("✓ Test packets created")


async def create_test_config():
    """Create test configuration file"""
    config = """
[hub]
url = "http://127.0.0.1:8000"
client_id = "test_client"
client_secret = "test_secret"

[bbs]
name = "Test BBS"
index = "02"

[sync]
sent_action = "archive"
archive_dir = "./sent"
max_retries = 3
retry_delay = 2
metrics_file = "./metrics.json"

[leagues.BRE.555]
enabled = true
outbound_dir = "./test_data/bre_555/outbound"
inbound_dir = "./test_data/bre_555/inbound"

[leagues.FE.123]
enabled = true
outbound_dir = "./test_data/fe_123/outbound"
inbound_dir = "./test_data/fe_123/inbound"
"""

    Path("test_config.toml").write_text(config)
    print("✓ Test config created")


async def create_inbound_packets_on_hub():
    """Create test packets on the hub for download"""
    import aiohttp

    url = "http://127.0.0.1:8000/test/create_packet"

    # Create packets from hub (01) to client (02)
    packets = [
        {"league": "555", "game": "BRE", "source": "01", "dest": "02", "sequence": 1},
        {"league": "555", "game": "BRE", "source": "01", "dest": "02", "sequence": 2},
        {"league": "123", "game": "FE", "source": "01", "dest": "02", "sequence": 1},
    ]

    async with aiohttp.ClientSession() as session:
        for packet in packets:
            async with session.post(url, json=packet) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    print(f"✓ Created test packet: {result['filename']}")
                else:
                    print(f"✗ Failed to create packet: {await resp.text()}")


async def run_client_test():
    """Run the client and verify results"""
    print("\n=== Running Client ===")

    import subprocess

    result = subprocess.run(
        ["python", "client.py", "--config", "test_config.toml"],
        capture_output=True,
        text=True,
    )

    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    print(f"\nExit code: {result.returncode}")

    # Check metrics
    if Path("metrics.json").exists():
        metrics = json.loads(Path("metrics.json").read_text())
        print("\n=== Metrics ===")
        print(json.dumps(metrics, indent=2))

    # Verify results
    print("\n=== Verification ===")

    # Check sent packets were archived
    sent_dir = Path("sent")
    sent_files = list(sent_dir.glob("*"))
    print(f"✓ Archived {len(sent_files)} sent packet(s)")

    # Check received packets
    bre_inbound = Path("test_data/bre_555/inbound")
    fe_inbound = Path("test_data/fe_123/inbound")

    bre_received = list(bre_inbound.glob("*"))
    fe_received = list(fe_inbound.glob("*"))

    print(f"✓ Received {len(bre_received)} BRE packet(s)")
    print(f"✓ Received {len(fe_received)} FE packet(s)")

    return result.returncode == 0


async def cleanup():
    """Clean up test files"""
    print("\n=== Cleanup ===")

    dirs_to_remove = ["test_data", "sent", "mock_hub_data"]
    files_to_remove = ["test_config.toml", "metrics.json"]

    for dir_path in dirs_to_remove:
        if Path(dir_path).exists():
            shutil.rmtree(dir_path)
            print(f"✓ Removed {dir_path}")

    for file_path in files_to_remove:
        if Path(file_path).exists():
            Path(file_path).unlink()
            print(f"✓ Removed {file_path}")


async def main():
    """Run full test suite"""
    print("=" * 60)
    print("Nova Hub Client Test Suite")
    print("=" * 60)

    try:
        # Setup
        await setup_test_environment()
        await create_test_config()

        print("\n⚠️  Make sure mock_hub.py is running on port 8000!")
        print("Run: python mock_hub.py")
        input("Press Enter when ready...")

        # Create packets on hub for download
        await create_inbound_packets_on_hub()

        # Run client
        success = await run_client_test()

        if success:
            print("\n✅ All tests passed!")
        else:
            print("\n❌ Tests failed!")

        # Ask about cleanup
        response = input("\nClean up test files? (y/n): ")
        if response.lower() == "y":
            await cleanup()

    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
