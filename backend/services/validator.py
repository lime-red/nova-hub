"""
Configuration validator for Nova Hub

Validates:
- Game, inbound, and outbound directories exist
- No duplicate directories across league entries
- Nodes.dat files exist and are parseable
- BBS indices and names match in nodes.dat
- Nodes.dat matches league+client configuration in database
"""

import os
from pathlib import Path
from typing import Dict, List, Tuple

import toml
from sqlalchemy.orm import Session

from backend.models.database import League, LeagueMembership, Client
from backend.services.nodes_parser import NodesFileParser


class ValidationError:
    """Represents a single validation error"""

    def __init__(self, category: str, message: str, severity: str = "ERROR"):
        self.category = category
        self.message = message
        self.severity = severity

    def __str__(self):
        return f"[{self.severity}] {self.category}: {self.message}"


class HubValidator:
    """Validates Nova Hub configuration"""

    def __init__(self, config_path: str = "config.toml", db_session: Session = None):
        self.config_path = config_path
        self.config = None
        self.db = db_session
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []

    def load_config(self) -> bool:
        """Load configuration file"""
        if not os.path.exists(self.config_path):
            self.errors.append(
                ValidationError("Config", f"Configuration file not found: {self.config_path}")
            )
            return False

        try:
            self.config = toml.load(self.config_path)
            return True
        except Exception as e:
            self.errors.append(
                ValidationError("Config", f"Failed to parse configuration: {e}")
            )
            return False

    def find_file_case_insensitive(self, directory: Path, filename: str) -> Path | None:
        """Find a file in directory with case-insensitive search"""
        if not directory.exists():
            return None

        filename_lower = filename.lower()
        try:
            for item in directory.iterdir():
                if item.is_file() and item.name.lower() == filename_lower:
                    return item
        except PermissionError:
            pass
        return None

    def validate_directory(self, dir_path: str, dir_type: str, league_key: str) -> bool:
        """Validate that a directory exists"""
        path = Path(dir_path)
        if not path.exists():
            self.errors.append(
                ValidationError(
                    "Directory",
                    f"{league_key}: {dir_type} directory does not exist: {dir_path}"
                )
            )
            return False
        if not path.is_dir():
            self.errors.append(
                ValidationError(
                    "Directory",
                    f"{league_key}: {dir_type} path is not a directory: {dir_path}"
                )
            )
            return False
        return True

    def check_duplicate_directories(self, leagues_config: dict) -> None:
        """Check for duplicate directories across all league entries"""
        # Track all directories used
        dir_usage: Dict[str, List[Tuple[str, str]]] = {}  # dir_path -> [(league_key, dir_type)]

        # leagues_config is {league_id: {game_type: settings}}
        for league_id, game_types in leagues_config.items():
            for game_type, league_settings in game_types.items():
                league_key = f"{game_type}.{league_id}"

                # Check game_folder
                game_folder = league_settings.get("game_folder")
                if game_folder:
                    abs_path = str(Path(game_folder).resolve())
                    dir_usage.setdefault(abs_path, []).append((league_key, "game_folder"))

                # Check outbound_folder
                outbound_folder = league_settings.get("outbound_folder")
                if outbound_folder:
                    abs_path = str(Path(outbound_folder).resolve())
                    dir_usage.setdefault(abs_path, []).append((league_key, "outbound_folder"))

                # Check inbound_folder
                inbound_folder = league_settings.get("inbound_folder")
                if inbound_folder:
                    abs_path = str(Path(inbound_folder).resolve())
                    dir_usage.setdefault(abs_path, []).append((league_key, "inbound_folder"))

        # Report duplicates
        for dir_path, usages in dir_usage.items():
            if len(usages) > 1:
                usage_str = ", ".join([f"{key} ({dtype})" for key, dtype in usages])
                self.errors.append(
                    ValidationError(
                        "Directory",
                        f"Directory used multiple times: {dir_path} - {usage_str}"
                    )
                )

    def validate_nodes_file_against_db(
        self, game_type: str, league_id: str, league_settings: dict
    ) -> None:
        """Validate nodes.dat file matches database configuration"""
        league_key = f"{game_type}.{league_id}"

        # Get league from database
        if not self.db:
            self.warnings.append(
                ValidationError(
                    "Database",
                    f"{league_key}: Database not available, skipping database validation",
                    severity="WARNING"
                )
            )
            return

        league = (
            self.db.query(League)
            .filter(League.league_id == league_id, League.game_type == game_type[0].upper())
            .first()
        )

        if not league:
            self.warnings.append(
                ValidationError(
                    "Database",
                    f"{league_key}: League not found in database",
                    severity="WARNING"
                )
            )
            return

        # Get game folder
        game_folder = league_settings.get("game_folder")
        if not game_folder:
            self.warnings.append(
                ValidationError(
                    "NodesFile",
                    f"{league_key}: game_folder not configured, skipping nodes file validation",
                    severity="WARNING"
                )
            )
            return

        game_folder_path = Path(game_folder)
        if not game_folder_path.exists():
            # Already reported by validate_directory
            return

        # Determine expected nodes file name
        nodes_filename = "brnodes.dat" if game_type.lower() == "bre" else "fenodes.dat"

        # Find file case-insensitively
        nodes_file = self.find_file_case_insensitive(game_folder_path, nodes_filename)

        if not nodes_file:
            self.errors.append(
                ValidationError(
                    "NodesFile",
                    f"{league_key}: {nodes_filename} not found in {game_folder}"
                )
            )
            return

        # Parse nodes file
        parser = NodesFileParser(nodes_file)
        if not parser.parse():
            for error in parser.errors:
                self.errors.append(
                    ValidationError(
                        "NodesFile",
                        f"{league_key}: {nodes_filename}: {error}"
                    )
                )
            return

        # Check for duplicate indices
        duplicates = parser.check_duplicate_indices()
        for dup in duplicates:
            self.errors.append(
                ValidationError(
                    "NodesFile",
                    f"{league_key}: {nodes_filename}: {dup}"
                )
            )

        # Get all active memberships for this league
        memberships = (
            self.db.query(LeagueMembership, Client)
            .join(Client, LeagueMembership.client_id == Client.id)
            .filter(
                LeagueMembership.league_id == league.id,
                LeagueMembership.is_active == True
            )
            .all()
        )

        # Check each membership against nodes file
        for membership, client in memberships:
            bbs_index = membership.bbs_index
            bbs_name = client.bbs_name
            fidonet_address = membership.fidonet_address

            # Find node in parsed file
            node = parser.get_node_by_index(bbs_index)
            if not node:
                self.errors.append(
                    ValidationError(
                        "NodesFile",
                        f"{league_key}: Client '{bbs_name}' (BBS index {bbs_index}) "
                        f"not found in {nodes_filename}"
                    )
                )
                continue

            # Check BBS name matches
            if node.bbs_name.strip().lower() != bbs_name.strip().lower():
                self.errors.append(
                    ValidationError(
                        "NodesFile",
                        f"{league_key}: BBS name mismatch for index {bbs_index} - "
                        f"database has '{bbs_name}' but {nodes_filename} has '{node.bbs_name}'"
                    )
                )

            # Check FidoNet address matches (if set in database)
            if fidonet_address:
                if node.fidonet_address.strip() != fidonet_address.strip():
                    self.errors.append(
                        ValidationError(
                            "NodesFile",
                            f"{league_key}: FidoNet address mismatch for '{bbs_name}' (index {bbs_index}) - "
                            f"database has '{fidonet_address}' but {nodes_filename} has '{node.fidonet_address}'"
                        )
                    )

        # Check for nodes in file that aren't in database
        db_indices = {m.bbs_index for m, _ in memberships}
        for node in parser.nodes:
            if node.bbs_index not in db_indices:
                self.warnings.append(
                    ValidationError(
                        "NodesFile",
                        f"{league_key}: {nodes_filename} contains BBS index {node.bbs_index} "
                        f"('{node.bbs_name}') which is not in the database",
                        severity="WARNING"
                    )
                )

    def validate(self) -> bool:
        """
        Run all validations.
        Returns True if all validations passed, False otherwise.
        """
        # Load config
        if not self.load_config():
            return False

        # Check required sections
        if "dosemu" not in self.config:
            self.errors.append(ValidationError("Config", "Missing [dosemu] section"))
            return False

        dosemu_config = self.config["dosemu"]

        # Parse league configurations from dosemu section
        # Format is [dosemu.{league_id}.{game_type}]
        # We need to extract all league_id.game_type combinations
        leagues_config = {}  # {league_id: {game_type: settings}}

        for key, value in dosemu_config.items():
            # Skip non-dict entries (like dosemu_path, config_dir, etc.)
            if not isinstance(value, dict):
                continue

            # This is a league_id
            league_id = key
            if league_id not in leagues_config:
                leagues_config[league_id] = {}

            # Now iterate over game types for this league
            for game_type, league_settings in value.items():
                if not isinstance(league_settings, dict):
                    continue

                leagues_config[league_id][game_type] = league_settings

        # Validate each league
        league_count = 0
        for league_id, game_types in leagues_config.items():
            for game_type, league_settings in game_types.items():
                league_count += 1
                league_key = f"{game_type}.{league_id}"

                # Validate directories
                # Note: Hub uses game_folder not game_dos_path for Linux path
                game_folder = league_settings.get("game_folder")
                if game_folder:
                    self.validate_directory(game_folder, "game_folder", league_key)
                else:
                    self.warnings.append(
                        ValidationError(
                            "Config",
                            f"{league_key}: game_folder not configured",
                            severity="WARNING"
                        )
                    )

                outbound_folder = league_settings.get("outbound_folder")
                if outbound_folder:
                    self.validate_directory(outbound_folder, "outbound_folder", league_key)
                else:
                    self.errors.append(
                        ValidationError(
                            "Config",
                            f"{league_key}: outbound_folder not configured"
                        )
                    )

                inbound_folder = league_settings.get("inbound_folder")
                if inbound_folder:
                    self.validate_directory(inbound_folder, "inbound_folder", league_key)
                else:
                    self.errors.append(
                        ValidationError(
                            "Config",
                            f"{league_key}: inbound_folder not configured"
                        )
                    )

                # Validate nodes file against database
                self.validate_nodes_file_against_db(game_type, league_id, league_settings)

        if league_count == 0:
            self.warnings.append(
                ValidationError(
                    "Config",
                    "No leagues found in configuration",
                    severity="WARNING"
                )
            )

        # Check for duplicate directories
        self.check_duplicate_directories(leagues_config)

        return len(self.errors) == 0

    def print_results(self) -> None:
        """Print validation results"""
        print("\n" + "=" * 70)
        print("Nova Hub - Configuration Validation")
        print("=" * 70)
        print()

        if self.warnings:
            print(f"Warnings: {len(self.warnings)}")
            for warning in self.warnings:
                print(f"  {warning}")
            print()

        if self.errors:
            print(f"Errors: {len(self.errors)}")
            for error in self.errors:
                print(f"  {error}")
            print()
        else:
            print("✓ All validation checks passed!")
            print()

        print("=" * 70)
        print()
