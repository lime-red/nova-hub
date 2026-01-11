#!/usr/bin/env python3
"""
Debug script to run DOSEMU interactively with the same config as automated processing.
This lets you test the exact batch file and configuration that would be used.

Usage:
    python debug_dosemu.py <league_id> <game_type> [command_key]

Examples:
    python debug_dosemu.py 013 bre processing_command
    python debug_dosemu.py 013 bre scores_command
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import toml


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    league_id = sys.argv[1]
    game_type = sys.argv[2].upper()
    command_key = sys.argv[3] if len(sys.argv) > 3 else "processing_command"

    # Load config
    config = toml.load("config.toml")

    # Get DOSEMU config
    try:
        dosemu_config = config["dosemu"]
        league_config = dosemu_config[league_id][game_type.lower()]
    except KeyError as e:
        print(
            f"ERROR: Configuration not found for league {league_id}, game {game_type}"
        )
        print(f"Missing key: {e}")
        sys.exit(1)

    # Get command and paths
    command = league_config.get(command_key)
    if not command:
        print(f"ERROR: Command '{command_key}' not found in config")
        print(f"Available commands: {list(league_config.keys())}")
        sys.exit(1)

    game_dos_path = league_config.get("game_dos_path", "C:\\")
    dosemu_path = config.get("dosemu", {}).get("dosemu_path", "/usr/bin/dosemu")
    data_dir = config.get("server", {}).get("data_dir", "./data")

    # Create working directory
    work_dir = Path(data_dir) / "dosemu" / league_id / game_type.lower()
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("DOSEMU Interactive Debug Session")
    print(f"{'=' * 60}")
    print(f"League:      {league_id}")
    print(f"Game:        {game_type}")
    print(f"Command:     {command_key}")
    print(f"DOS Path:    {game_dos_path}")
    print(f"Work Dir:    {work_dir}")
    print(f"{'=' * 60}\n")

    # Generate DOSEMU config
    config_dir_str = config.get("dosemu", {}).get("config_dir", "./dosemu_configs")
    config_dir = Path(config_dir_str)
    if not config_dir.is_absolute():
        config_dir = Path(data_dir) / config_dir
    config_dir.mkdir(parents=True, exist_ok=True)

    conf_file = config_dir / f"{game_type.lower()}_debug.conf"

    conf_content = f"""# DOSEMU Debug Configuration for {game_type} League {league_id}
# Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

# Keyboard layout
$_layout = "us"

# No floppy drives
$_floppy_a = ""

# Memory settings (use brackets for numeric literals)
$_xms = (8192)
$_ems = (8192)
# DPMI removed - causes segfault and BRE/FE doesn't need it
# $_dpmi = (0x4000)

# Video for interactive session (in config, not command line)
$_X = ""
$_vga = "vga"
$_term_update_freq = (4)

# Serial/Network off
$_com1 = ""
$_com2 = ""
"""

    conf_file.write_text(conf_content)
    print(f"Created config: {conf_file}")

    # Create batch file
    batch_file = work_dir / "PROCESS.BAT"

    # Extract drive letter if present (e.g., "C:" from "C:\path\to\game")
    drive_letter = ""
    if len(game_dos_path) >= 2 and game_dos_path[1] == ':':
        drive_letter = game_dos_path[:2]  # e.g., "C:"

    batch_content = f"""@ECHO OFF
ECHO.
ECHO ============================================================
ECHO Nova Hub Debug - League {league_id} - {game_type}
ECHO ============================================================
ECHO.
ECHO Changing to: {game_dos_path}
"""

    # Add drive change if we have a drive letter
    # In DOS, CD doesn't change drives - you must explicitly switch drives first
    if drive_letter:
        batch_content += f"{drive_letter}\n"  # Change to drive (e.g., "C:")

    batch_content += f"""CD {game_dos_path}
CD
ECHO.
ECHO Running: {command}
ECHO.
{command}
ECHO.
ECHO ============================================================
ECHO Processing Complete - Press any key to exit
ECHO ============================================================
PAUSE
"""
    batch_file.write_text(batch_content)
    print(f"Created batch: {batch_file}")
    print("\nBatch file contents:")
    print("-" * 60)
    print(batch_content)
    print("-" * 60)

    # Ask if user wants to continue
    print("\nReady to launch DOSEMU interactively.")
    print("The batch file will run automatically when DOSEMU starts.")
    response = input("Continue? [Y/n]: ")
    if response.lower() in ["n", "no"]:
        print("Aborted.")
        sys.exit(0)

    # Build DOSEMU command for interactive session
    # Video settings are in the config file, not command line
    cmd = [
        dosemu_path,
        "-f",
        str(conf_file),
        str(batch_file),  # Full path to batch file
    ]

    print(f"\nRunning: {' '.join(cmd)}\n")
    print("=" * 60)

    # Run DOSEMU interactively
    try:
        result = subprocess.run(cmd, cwd=str(work_dir))
        print("\n" + "=" * 60)
        print(f"DOSEMU exited with code: {result.returncode}")
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    print("\nDebug session complete.")
    print(f"Batch file saved at: {batch_file}")
    print(f"Config file saved at: {conf_file}")


if __name__ == "__main__":
    main()
