# run.py - Complete startup script

import sys
from pathlib import Path

import toml
import uvicorn



def main():
    """Start Nova Hub"""

    # Load config to get data directory
    config = toml.load("config.toml")
    data_dir = config.get("server", {}).get("data_dir", "./data")

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
        "static",
        "templates",
    ]

    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)

    print("""
    ╔══════════════════════════════════════╗
    ║         Nova Hub v0.1.0              ║
    ║  BBS Inter-League Routing System     ║
    ╚══════════════════════════════════════╝
    """)

    print("Starting server...")
    print("Web UI: http://localhost:8000")
    print("API Docs: http://localhost:8000/docs")
    print("API Redoc: http://localhost:8000/redoc")
    print()

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Disable in production
        log_level="info",
    )


if __name__ == "__main__":
    main()
