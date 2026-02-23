#!/usr/bin/env python3
"""
Create default admin user using direct SQL
Run with: python create_admin_sql.py

Password resolution order:
  1. --password CLI argument
  2. NOVA_HUB_ADMIN_PASSWORD environment variable
  3. (no default — a random password is generated if neither is set)
"""

import os
import secrets
import sqlite3
import sys
import bcrypt
import toml
from datetime import datetime

# --- Password resolution ---
password = None

if len(sys.argv) == 3 and sys.argv[1] == "--password":
    password = sys.argv[2]
elif len(sys.argv) == 2 and sys.argv[1].startswith("--password="):
    password = sys.argv[1].split("=", 1)[1]
else:
    password = os.environ.get("NOVA_HUB_ADMIN_PASSWORD")

generated = False
if not password:
    # Generate a secure random password if none was supplied
    password = secrets.token_urlsafe(16)
    generated = True

# Load database path from config
config = toml.load("config.toml")
db_path = config.get("database", {}).get("path", "./data/nova-hub.db")

# Hash password directly with bcrypt
hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# Connect to database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Check if admin already exists
    cursor.execute("SELECT username FROM sysop_users WHERE username = ?", ("admin",))
    existing = cursor.fetchone()

    if existing:
        print("=" * 60)
        print("Admin user already exists — password unchanged.")
        print("=" * 60)
    else:
        # Insert admin user
        cursor.execute("""
            INSERT INTO sysop_users
            (username, email, hashed_password, full_name, is_active, is_superuser, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            "admin",
            "admin@localhost",
            hashed,
            "System Administrator",
            1,  # is_active
            1,  # is_superuser
            datetime.utcnow().isoformat()
        ))

        conn.commit()

        print("=" * 60)
        print("Default admin user created successfully!")
        print("=" * 60)
        print()
        print("Login credentials:")
        print("  Username: admin")
        if generated:
            print(f"  Password: {password}  (auto-generated — save this now)")
        else:
            print("  Password: (as configured)")
        print()
        print("=" * 60)

except Exception as e:
    print(f"Error creating admin user: {e}")
    import traceback
    traceback.print_exc()
finally:
    conn.close()
