#!/usr/bin/env python3
"""
Generate a secure JWT secret key for Nova Hub

Run with: python generate_jwt_secret.py
"""

import secrets

# Generate a cryptographically secure random string
jwt_secret = secrets.token_urlsafe(64)

print("=" * 70)
print("Generated Secure JWT Secret")
print("=" * 70)
print()
print("Add this to your config.toml under [security]:")
print()
print(f'jwt_secret = "{jwt_secret}"')
print()
print("=" * 70)
print()
print("⚠️  IMPORTANT:")
print("  - Keep this secret secure")
print("  - Never commit it to version control")
print("  - Changing this will invalidate all existing JWT tokens")
print()
