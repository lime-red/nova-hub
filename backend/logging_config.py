# backend/logging_config.py - Loguru logging configuration

import os
import sys
from contextvars import ContextVar
from loguru import logger

# Context variable tracking the authenticated user for the current request.
# Set by AuthContextMiddleware; defaults to "-" (unauthenticated / background).
current_request_user: ContextVar[str] = ContextVar("current_request_user", default="-")


def _patcher(record):
    """Patch log records to ensure 'context' and 'user' always exist in extra."""
    if "context" not in record["extra"]:
        record["extra"]["context"] = "system"
    if "user" not in record["extra"]:
        record["extra"]["user"] = current_request_user.get()


def configure_logging(log_level: str = "INFO"):
    """
    Configure loguru logging for Nova Hub.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO
    """
    # Remove default logger
    logger.remove()

    # Add colorized stdout handler with custom format
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[context]: <20}</cyan> | "
            "<yellow>{extra[user]: <15}</yellow> | "
            "<level>{message}</level>"
        ),
        level=log_level,
        colorize=True,
    )

    # Configure patcher to add default context/user
    logger.configure(patcher=_patcher)

    logger.info(f"Logging configured at {log_level} level")


def get_logger(context: str):
    """
    Get a logger bound to a specific context.

    Args:
        context: The context/service name (e.g., 'processing', 'auth', 'watcher')

    Returns:
        Logger instance bound to the specified context

    Example:
        logger = get_logger(context="processing")
        logger.info("Starting batch processing")
    """
    return logger.bind(context=context)


def init_logging_from_config(config: dict):
    """
    Initialize logging from config.toml configuration.

    Args:
        config: Configuration dictionary loaded from config.toml
    """
    # Get log level from config or environment variable
    log_level = os.getenv("NOVA_HUB_LOG_LEVEL")

    if not log_level:
        log_level = config.get("logging", {}).get("level", "INFO")

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    log_level = log_level.upper()

    if log_level not in valid_levels:
        log_level = "INFO"

    configure_logging(log_level)
