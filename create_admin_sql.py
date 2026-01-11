#!/usr/bin/env python3
"""
Create default admin user using direct SQL
Run with: python create_admin_sql.py
"""

import sqlite3
import bcrypt
from datetime import datetime

# Database path
db_path = "/home/lime/nova-data/nova-hub.db"

# Hash password directly with bcrypt
password = "admin"
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
        print("Admin user already exists!")
        print("=" * 60)
        print()
        print("Username: admin")
        print()
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
        print("  Password: admin")
        print()
        print("⚠️  IMPORTANT: Change this password immediately in production!")
        print("   Go to: http://localhost:8000/admin/users")
        print()
        print("=" * 60)

except Exception as e:
    print(f"Error creating admin user: {e}")
    import traceback
    traceback.print_exc()
finally:
    conn.close()
