# backend/services/dosemu_runner.py

import asyncio
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


class DosemuRunner:
    def __init__(self, config):
        self.config = config
        # Handle both dict and object-style config
        if isinstance(config, dict):
            self.dosemu_path = config["dosemu"]["dosemu_path"]
            self.dosemu_timeout = config["dosemu"]["timeout"]
            self.server_data_dir = config["server"]["data_dir"]
        else:
            self.dosemu_path = config.dosemu.dosemu_path
            self.dosemu_timeout = config.dosemu.timeout
            self.server_data_dir = config.server.data_dir

    async def run_game_process(self, game_type: str, league_id: str) -> dict:
        """
        Run BRE or FE processing command in Dosemu for specific league
        Returns: dict with status, output, errors
        """
        return await self._run_dosemu_command(game_type, league_id, "processing_command")

    async def run_scores_command(self, game_type: str, league_id: str) -> dict:
        """
        Run BRE or FE scores command in Dosemu for specific league
        Returns: dict with status, output, errors
        """
        return await self._run_dosemu_command(game_type, league_id, "scores_command")

    async def run_routes_command(self, game_type: str, league_id: str) -> dict:
        """
        Run BRE or FE routeinfo command in Dosemu for specific league
        Returns: dict with status, output, errors
        """
        return await self._run_dosemu_command(game_type, league_id, "routeinfo_command")

    async def run_bbsinfo_command(self, game_type: str, league_id: str) -> dict:
        """
        Run BRE or FE bbsinfo command in Dosemu for specific league
        Returns: dict with status, output, errors
        """
        return await self._run_dosemu_command(game_type, league_id, "bbsinfo_command")

    async def _run_dosemu_command(self, game_type: str, league_id: str, command_key: str) -> dict:
        """
        Internal method to run any DOS command in Dosemu
        """
        # Get league-specific config: config["dosemu"][league_id][game_type]
        try:
            if isinstance(self.config, dict):
                league_config = self.config["dosemu"][league_id][game_type.lower()]
            else:
                league_config = getattr(getattr(self.config.dosemu, league_id), game_type.lower())
        except (KeyError, AttributeError):
            return {
                "status": "error",
                "error": f"No DOSEMU configuration found for league {league_id}, game {game_type}",
                "returncode": -1
            }

        # Get the command to run
        if isinstance(league_config, dict):
            command = league_config.get(command_key)
            game_dos_path = league_config.get("game_dos_path", "C:\\")
        else:
            command = getattr(league_config, command_key, None)
            game_dos_path = getattr(league_config, "game_dos_path", "C:\\")

        if not command:
            return {
                "status": "error",
                "error": f"Missing {command_key} for league {league_id}, game {game_type}",
                "returncode": -1
            }

        # Build per-league directory path
        drive_path = Path(self.server_data_dir) / "dosemu" / league_id / game_type.lower()
        drive_path.mkdir(parents=True, exist_ok=True)

        # Create inbound and outbound directories
        inbound_dir = drive_path / "inbound"
        outbound_dir = drive_path / "outbound"
        inbound_dir.mkdir(parents=True, exist_ok=True)
        outbound_dir.mkdir(parents=True, exist_ok=True)

        # Create dosemu configuration on the fly
        dosemu_conf = self._generate_dosemu_conf(game_type, drive_path)

        # Create temporary batch file to run the command
        batch_file = drive_path / "PROCESS.BAT"
        self._create_batch_file(batch_file, command, game_dos_path)

        # Prepare output capture
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = Path(self.server_data_dir) / "logs" / "dosemu"
        log_dir.mkdir(parents=True, exist_ok=True)

        output_log = log_dir / f"{league_id}_{game_type.lower()}_{command_key}_{timestamp}.log"

        try:
            # Run dosemu
            cmd = [
                self.dosemu_path,
                "-f",
                str(dosemu_conf),
                str(batch_file),  # Full path to batch file
            ]

            result = await asyncio.wait_for(
                self._run_command(cmd, output_log), timeout=self.dosemu_timeout
            )

            # Parse output for any useful information
            output_text = self._parse_dosemu_output(output_log)

            return {
                "status": "success" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                "output": output_text,
                "log_file": str(output_log),
            }

        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "error": f"Processing timed out after {self.dosemu_timeout}s",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
        finally:
            # Cleanup batch file
            batch_file.unlink(missing_ok=True)

    async def _run_command(self, cmd: list, output_log: Path):
        """Run command with script wrapper to capture all output including ANSI codes"""
        import shlex

        # Build script command to capture output
        dosemu_cmd = " ".join([shlex.quote(str(c)) for c in cmd])
        script_cmd = [
            "script",
            "-c",
            dosemu_cmd,
            str(output_log)
        ]

        # Still capture stdout/stderr in case script fails to start
        process = await asyncio.create_subprocess_exec(
            *script_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.server_data_dir,
        )

        # Read output (will be empty if script works, but captures script errors)
        stdout_data, _ = await process.communicate()

        # If script failed, write the error output to log file
        if process.returncode != 0 and stdout_data:
            with open(output_log, 'wb') as f:
                f.write(stdout_data)

        # Return a simple object with returncode attribute
        class Result:
            def __init__(self, rc):
                self.returncode = rc
        return Result(process.returncode)

    def _generate_dosemu_conf(self, game_type: str, drive_path: Path) -> Path:
        """Generate dosemu configuration file"""
        # Handle both dict and object-style config
        if isinstance(self.config, dict):
            config_dir_str = self.config.get("dosemu", {}).get("config_dir", "./dosemu_configs")
        else:
            config_dir_str = self.config.dosemu.config_dir

        # Make config_dir relative to server_data_dir if it's a relative path
        config_dir = Path(config_dir_str)
        if not config_dir.is_absolute():
            config_dir = Path(self.server_data_dir) / config_dir

        config_dir.mkdir(parents=True, exist_ok=True)

        conf_file = config_dir / f"{game_type.lower()}.conf"

        # Basic dosemu configuration
        # Uses default drive_c mapping (/home/lime/.dosemu/drive_c -> C:)
        conf_content = f"""# Keyboard layout
$_layout = "us"

# No floppy drives
$_floppy_a = ""

# Memory settings (use brackets for numeric literals)
$_xms = (8192)
$_ems = (8192)
# DPMI removed - causes segfault and BRE/FE doesn't need it
# $_dpmi = (0x4000)

# No video needed (headless mode)
$_X = ""
$_vga = "off"
$_graphics = "off"

# Serial/Network off
$_com1 = ""
$_com2 = ""

# Quiet mode
$_quiet = (1)
"""

        conf_file.write_text(conf_content)
        return conf_file

    def _create_batch_file(self, batch_file: Path, command: str, game_dos_path: str = "C:\\"):
        """Create DOS batch file to run the game command"""
        # Extract drive letter if present (e.g., "C:" from "C:\path\to\game")
        drive_letter = ""
        if len(game_dos_path) >= 2 and game_dos_path[1] == ':':
            drive_letter = game_dos_path[:2]  # e.g., "C:"

        batch_content = "@ECHO OFF\nECHO Nova Hub Processing Starting\n"

        # Add drive change if we have a drive letter
        # In DOS, CD doesn't change drives - you must explicitly switch drives first
        if drive_letter:
            batch_content += f"ECHO Changing to: {game_dos_path}\n"
            batch_content += f"{drive_letter}\n"  # Change to drive (e.g., "C:")
            batch_content += f"CD {game_dos_path}\n"
            batch_content += "CD\n"  # Echo current directory
        else:
            batch_content += f"CD {game_dos_path}\n"

        batch_content += f"{command}\n"
        batch_content += "ECHO Nova Hub Processing Complete\n"
        batch_content += "EXIT\n"

        batch_file.write_text(batch_content)

    def _parse_dosemu_output(self, log_file: Path) -> str:
        """
        Read dosemu output log (raw with ANSI codes)
        ANSI codes will be converted to HTML on display
        """
        if not log_file.exists():
            return ""

        # Read and return raw output including ANSI codes
        output = log_file.read_text(errors="replace")

        # Don't truncate - store full output
        return output
