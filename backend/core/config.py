"""
Centralized configuration management for Nova Hub

Loads configuration from config.toml and provides typed access via Pydantic.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import toml
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    """Server configuration"""
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "./data"
    environment: str = "production"


class HubConfig(BaseModel):
    """Hub identification"""
    bbs_name: str = "Nova Hub"
    bbs_index: str = "01"
    # Location lines for the hub's own entry in generated nodelists. The
    # hub's FidoNet address is not here because it differs per league; it
    # lives on League.hub_fidonet_address.
    city: str = ""
    state: str = ""
    country: str = ""


class ProcessingConfig(BaseModel):
    """Packet processing configuration"""
    poll_interval: int = 60
    # Days of bulk content to keep. Dosemu transcripts and generated file bodies
    # older than this are dropped; the rows themselves, and every count on them,
    # are kept. 0 disables the pass. See backend/services/retention_service.py.
    retention_days: int = 30
    retention_batch_size: int = 500


class DosemuConfig(BaseModel):
    """Dosemu configuration"""
    dosemu_path: str = "/usr/bin/dosemu"
    config_dir: str = "./dosemu_configs"
    capture_output: bool = True
    timeout: int = 300
    # TERM passed to dosemu. It refuses to start under TERM=dumb, which is what
    # `script` supplies when the hub has no controlling terminal.
    term: str = "linux"


class DatabaseConfig(BaseModel):
    """Database configuration"""
    path: str = "./data/nova-hub.db"


class SecurityConfig(BaseModel):
    """Security configuration"""
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_hours: int = 24
    max_upload_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    min_password_length: int = 12
    cookie_secure: bool = True


class RateLimitingConfig(BaseModel):
    """Rate limiting configuration"""
    enabled: bool = True
    auth_attempts_per_minute: int = 10
    auth_lockout_seconds: int = 300


class EmailAlertConfig(BaseModel):
    """SMTP email alerting configuration"""
    enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = "nova-hub@localhost"
    to_addresses: List[str] = []
    use_tls: bool = True
    timeout: int = 10


class WebhookAlertConfig(BaseModel):
    """Webhook alerting configuration"""
    enabled: bool = False
    url: str = ""
    secret: str = ""  # HMAC-SHA256 signing secret; empty disables signing
    timeout_seconds: int = 10
    retry_attempts: int = 3


class AlertingConfig(BaseModel):
    """Out-of-band alert delivery configuration"""
    enabled: bool = False
    email: EmailAlertConfig = EmailAlertConfig()
    webhook: WebhookAlertConfig = WebhookAlertConfig()


class Config(BaseModel):
    """Main configuration container"""
    server: ServerConfig = ServerConfig()
    hub: HubConfig = HubConfig()
    processing: ProcessingConfig = ProcessingConfig()
    dosemu: DosemuConfig = DosemuConfig()
    database: DatabaseConfig = DatabaseConfig()
    security: SecurityConfig = SecurityConfig()
    rate_limiting: RateLimitingConfig = RateLimitingConfig()
    alerting: AlertingConfig = AlertingConfig()

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

    alerting_raw = dict(raw_config.get("alerting", {}))
    email_raw = dict(alerting_raw.get("email", {}))
    webhook_raw = dict(alerting_raw.get("webhook", {}))
    alerting_base = {k: v for k, v in alerting_raw.items() if k not in ("email", "webhook")}

    _config = Config(
        server=ServerConfig(**raw_config.get("server", {})),
        hub=HubConfig(**raw_config.get("hub", {})),
        processing=ProcessingConfig(**raw_config.get("processing", {})),
        dosemu=DosemuConfig(**raw_config.get("dosemu", {})),
        database=DatabaseConfig(**raw_config.get("database", {})),
        security=SecurityConfig(**raw_config.get("security", {})),
        rate_limiting=RateLimitingConfig(**raw_config.get("rate_limiting", {})),
        alerting=AlertingConfig(
            **alerting_base,
            email=EmailAlertConfig(**email_raw),
            webhook=WebhookAlertConfig(**webhook_raw),
        ),
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
