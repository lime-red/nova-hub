#!/usr/bin/env python3
"""
Create default admin user for Nova Hub
Run with: python create_admin.py
"""

import sys
import toml
from app.database import init_database, create_default_admin

# Load config
config = toml.load("config.toml")
db_path = config.get("database", {}).get("path", "/home/lime/nova-data/nova-hub.db")
database_url = f"sqlite:///{db_path}"

# Initialize database
engine = init_database(database_url)

# Import SessionLocal after initialization
from app.database import SessionLocal

# Create session
db = SessionLocal()

try:
    from app.database import SysopUser
    from passlib.context import CryptContext
    from datetime import datetime

    # Check if admin already exists
    existing = db.query(SysopUser).filter(SysopUser.username == "admin").first()

    if existing:
        print("=" * 60)
        print("Admin user already exists!")
        print("=" * 60)
        print()
        print("Username: admin")
        print()
    else:
        # Create password hash
        pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed_password = pwd_context.hash("admin")

        # Create admin user
        admin = SysopUser(
            username="admin",
            email="admin@localhost",
            hashed_password=hashed_password,
            full_name="System Administrator",
            is_active=True,
            is_superuser=True,
            created_at=datetime.utcnow()
        )

        db.add(admin)
        db.commit()

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
    sys.exit(1)
finally:
    db.close()
