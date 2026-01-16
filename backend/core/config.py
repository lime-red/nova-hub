"""
Centralized configuration management for Nova Hub

Loads configuration from config.toml and provides typed access via Pydantic.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import toml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    """Server configuration"""
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "/home/lime/nova-data"
    environment: str = "production"


class HubConfig(BaseModel):
    """Hub identification"""
    bbs_name: str = "Nova Hub"
    bbs_index: str = "01"


class ProcessingConfig(BaseModel):
    """Packet processing configuration"""
    hold_time_hours: int = 0
    poll_interval: int = 60
    retention_days: int = 30


class DosemuConfig(BaseModel):
    """Dosemu configuration"""
    dosemu_path: str = "/usr/bin/dosemu"
    config_dir: str = "./dosemu_configs"
    capture_output: bool = True
    timeout: int = 300


class DatabaseConfig(BaseModel):
    """Database configuration"""
    path: str = "/home/lime/nova-data/nova-hub.db"


class SecurityConfig(BaseModel):
    """Security configuration"""
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_hours: int = 24


class RateLimitingConfig(BaseModel):
    """Rate limiting configuration"""
    enabled: bool = False
    requests_per_minute: int = 30


class Config(BaseModel):
    """Main configuration container"""
    server: ServerConfig = ServerConfig()
    hub: HubConfig = HubConfig()
    processing: ProcessingConfig = ProcessingConfig()
    dosemu: DosemuConfig = DosemuConfig()
    database: DatabaseConfig = DatabaseConfig()
    security: SecurityConfig = SecurityConfig()
    rate_limiting: RateLimitingConfig = RateLimitingConfig()

    # Raw config for accessing per-league dosemu settings
    _raw: Dict[str, Any] = {}

    class Config:
        arbitrary_types_allowed = True

    def get_league_dosemu_config(self, league_number: str, game_type: str) -> Optional[Dict[str, Any]]:
        """Get per-league dosemu configuration"""
        game_key = "bre" if game_type.upper() == "B" else "fe"
        key = f"dosemu.{league_number}.{game_key}"
        return self._raw.get("dosemu", {}).get(f"{league_number}", {}).get(game_key)

    def get(self, key: str, default: Any = None) -> Any:
        """Dictionary-style access for backwards compatibility"""
        parts = key.split(".")
        current = self._raw
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current


# Global config instance
_config: Optional[Config] = None


def load_config(config_path: str = "config.toml") -> Config:
    """Load configuration from TOML file"""
    global _config

    raw_config = toml.load(config_path)

    _config = Config(
        server=ServerConfig(**raw_config.get("server", {})),
        hub=HubConfig(**raw_config.get("hub", {})),
        processing=ProcessingConfig(**raw_config.get("processing", {})),
        dosemu=DosemuConfig(**raw_config.get("dosemu", {})),
        database=DatabaseConfig(**raw_config.get("database", {})),
        security=SecurityConfig(**raw_config.get("security", {})),
        rate_limiting=RateLimitingConfig(**raw_config.get("rate_limiting", {})),
    )
    _config._raw = raw_config

    return _config


def get_config() -> Config:
    """Get the current config instance, loading if necessary"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


# Expose config as module-level variable for convenience
# This will be initialized when the module is imported from main.py
config: Config = None  # type: ignore


def init_config(config_path: str = "config.toml") -> Config:
    """Initialize the global config instance"""
    global config
    config = load_config(config_path)
    return config
