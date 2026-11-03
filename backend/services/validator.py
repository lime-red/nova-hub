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
from typing import List

import toml
from sqlalchemy.orm import Session

from backend.models.database import League, LeagueMembership, Client
from backend.services.games import game_for_code
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

    def validate_nodes_file_against_db(
        self, game_type: str, league_id: str, league_settings: dict
    ) -> None:
        """Validate nodes.dat file matches database configuration"""
        league_key = f"{game_type}.{league_id}"

        try:
            game = game_for_code(game_type)
        except KeyError:
            self.errors.append(
                ValidationError("Config", f"{league_key}: unknown game '{game_type}'")
            )
            return

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
            .filter(League.league_id == league_id, League.game_type == game.letter)
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
        nodes_filename = game.nodes_file

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

        # Check for nodes in file that aren't in database.
        # The hub itself is deliberately not a membership -- it has no client
        # row -- so its own index is expected here rather than missing.
        db_indices = {m.bbs_index for m, _ in memberships}
        hub_index = self._hub_index()
        if hub_index is not None:
            db_indices.add(hub_index)
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

    def _hub_index(self) -> int | None:
        """The hub's own node number from [hub] bbs_index (a string like "01")."""
        raw = (self.config or {}).get("hub", {}).get("bbs_index")
        try:
            return int(str(raw), 10)
        except (TypeError, ValueError):
            return None

    def validate(self) -> bool:
        """
        Run all validations.
        Returns True if all validations passed, False otherwise.
        """
        from backend.services import preflight
        from backend.services.league_games import games_config, legacy_sections

        if not self.load_config():
            return False

        legacy = legacy_sections(self.config)
        for section in legacy:
            self.errors.append(ValidationError(
                "Config", f"{section}: leagues' games are in the database now; run "
                          f"migrate_league_games.py, or delete the section"))
        if "dosemu" not in self.config:
            self.errors.append(ValidationError("Config", "Missing [dosemu] section"))

        cfg = games_config(self.config)
        if cfg.root is None:
            self.errors.append(ValidationError("Config", "[games] root is not set"))
        else:
            self.validate_directory(str(cfg.root), "root", "games")
            if cfg.drive_c is not None and not (cfg.root == cfg.drive_c
                                                or cfg.drive_c in cfg.root.parents):
                self.errors.append(ValidationError(
                    "Config", f"[games] root {cfg.root} is not inside drive_c {cfg.drive_c}, so "
                              f"dosemu cannot see the DOS games"))
        from backend.services.image_fetch import config_problems as fetch_problems
        for problem in fetch_problems(cfg):
            self.errors.append(ValidationError("Config", problem))

        if not self.db:
            self.warnings.append(ValidationError(
                "Database", "Database not available, skipping the leagues' games",
                severity="WARNING"))
            return len(self.errors) == 0

        leagues = [l for l in self.db.query(League).all() if l.install is not None]
        if not leagues:
            self.warnings.append(ValidationError("Config", "No league has a game at the hub",
                                                 severity="WARNING"))
        if any(l.game_type == "I" for l in leagues) and self._hub_index() != 1:
            self.errors.append(ValidationError(
                "Config", f"Immortal Barons needs the hub at node 1, the League Coordinator, "
                          f"and [hub] bbs_index is "
                          f"{(self.config or {}).get('hub', {}).get('bbs_index')!r}"))
        if cfg.root is not None and not legacy:
            for full_id, problems in preflight.run(self.db, self.config, leagues).items():
                for problem in problems:
                    self.errors.append(ValidationError("Preflight", f"{full_id}: {problem}"))

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
