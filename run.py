# run.py - Complete startup script

import sys
from pathlib import Path

import toml
import uvicorn
from backend.logging_config import configure_logging, get_logger

# Initialize logging for this script
configure_logging(log_level="INFO")
logger = get_logger(context="startup")



def main():
    """Start Nova Hub"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Nova Hub - BBS Inter-League Routing System"
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to configuration file (default: config.toml)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate configuration without starting server"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Development mode: enable auto-reload and verbose output (do NOT use in production)"
    )

    args = parser.parse_args()

    # Handle validation mode
    if args.validate:
        from backend.services.validator import HubValidator
        from backend.core.database import init_database, get_db

        config = toml.load(args.config)

        # Initialize database for validation
        db_path = config.get("database", {}).get("path", "./data/nova-hub.db")
        database_url = f"sqlite:///{db_path}"
        init_database(database_url)

        db_session = next(get_db())
        try:
            validator = HubValidator(args.config, db_session)
            success = validator.validate()
            validator.print_results()
            sys.exit(0 if success else 1)
        finally:
            db_session.close()
        return

    # Normal operation mode - start server
    # Load config to get server settings
    config = toml.load(args.config)
    server_cfg = config.get("server", {})
    data_dir = server_cfg.get("data_dir", "./data")
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 8000))

    # Create required directories
    dirs = [
        data_dir,
        f"{data_dir}/packets/inbound",
        f"{data_dir}/packets/outbound",
        f"{data_dir}/packets/processed",
        f"{data_dir}/dosemu/bre/inbound",
        f"{data_dir}/dosemu/bre/outbound",
        f"{data_dir}/dosemu/fe/inbound",
        f"{data_dir}/dosemu/fe/outbound",
        f"{data_dir}/logs",
    ]

    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    logger.info("""
    ╔══════════════════════════════════════╗
    ║         Nova Hub v0.2.0              ║
    ║  BBS Inter-League Routing System     ║
    ╚══════════════════════════════════════╝
    """)

    if args.dev:
        logger.warning("Starting in DEVELOPMENT mode (auto-reload enabled, do not use in production)")

    logger.info("Starting server...")
    logger.info(f"Listening on {host}:{port}")
    logger.info(f"Web UI: http://{host}:{port}")
    logger.info(f"Service API Docs: http://{host}:{port}/service/docs")
    logger.info(f"Management API Docs: http://{host}:{port}/management/docs")
    logger.info("")

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=args.dev,
        log_level="info",
    )


if __name__ == "__main__":
    main()
