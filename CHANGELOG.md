# Changelog

All notable changes to Nova Hub will be documented in this file.

## [Unreleased]

### Added
- Initial MVP release
- Complete database schema with Alembic migrations
- OAuth2 authentication for BBS clients
- Web dashboard with real-time statistics
- Packet upload/download API
- WebSocket support for live updates
- Sequence validation and alerting
- Admin user management
- BBS client management
- Comprehensive documentation

### Fixed (2026-01-08)
- Fixed missing `alembic/env.py` configuration file
- Fixed missing `alembic/script.py.mako` template
- Fixed database path configuration to separate code and data directories
- Added missing `routers/auth.py` OAuth authentication module
- Added missing `app/services/packet_service.py` service
- Fixed database field name mismatches in `routers/web.py`:
  - `is_admin` → `is_superuser`
  - `password_hash` → `hashed_password`
  - `league.league_number` → `league.league_id`
  - `Packet.received_at` → `Packet.uploaded_at`
  - `packet.retrieved_at` → `packet.downloaded_at`
  - `Packet.generated_by_run` → `Packet.processing_run_id`
  - `run.error_message` → `run.stderr_log`
  - `run.dosemu_output` → `run.dosemu_log`
  - `client_secret_hash` → `client_secret`
- Fixed `main.py` database initialization to use config.toml
- Added missing `Session` import in `routers/web.py`
- Created `create_admin_sql.py` script to work around passlib/bcrypt compatibility issues
- Pinned bcrypt version to 4.0.1 in requirements.txt

### Documentation
- Updated `docs/SETUP_GUIDE.md` with complete installation instructions
- Created `docs/QUICKSTART.md` for rapid deployment
- Updated `README.md` with corrected quick start steps
- Added troubleshooting sections for common issues
- Added systemd service example for production deployment

## Version History

### [0.1.0] - 2026-01-08
- Initial working release
- All core features implemented
- Database migrations working
- Web interface functional
- API endpoints operational
- Admin user creation working

## Migration Notes

### From Pre-Release to 0.1.0

If you have an old database with incorrect schema:

```bash
# Backup existing data
cp /home/lime/nova-data/nova-hub.db ~/backups/

# Remove old database and migrations
rm /home/lime/nova-data/nova-hub.db
rm -rf alembic/versions/*.py

# Recreate with new schema
.venv/bin/alembic revision --autogenerate -m "Initial schema"
.venv/bin/alembic upgrade head

# Recreate admin user
.venv/bin/python create_admin_sql.py
```

## Known Issues

### passlib/bcrypt Compatibility
- passlib 1.7.4 has compatibility issues with newer bcrypt versions
- Workaround: Use `create_admin_sql.py` which uses bcrypt directly
- Long-term solution: Migrate to a more modern password hashing library

### Template References
- Some old field names may still exist in templates
- These will be fixed as they're encountered in testing

## Upgrade Instructions

### To Future Versions

When upgrading Nova Hub:

1. **Backup database:**
   ```bash
   cp /home/lime/nova-data/nova-hub.db ~/backups/nova-hub-$(date +%Y%m%d).db
   ```

2. **Update code:**
   ```bash
   git pull  # or download new version
   ```

3. **Update dependencies:**
   ```bash
   uv pip install -r requirements.txt
   ```

4. **Run migrations:**
   ```bash
   .venv/bin/alembic upgrade head
   ```

5. **Restart service:**
   ```bash
   sudo systemctl restart nova-hub
   # or
   .venv/bin/uvicorn main:app --reload
   ```

## Contributing

When making changes:
1. Update relevant documentation
2. Add entry to this changelog
3. Create database migration if schema changes
4. Update requirements.txt if dependencies change
5. Test upgrade path from previous version
