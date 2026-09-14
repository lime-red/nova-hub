# Changelog

All notable changes to Nova Hub will be documented in this file.

## [Unreleased]

## [0.3.1] - 2026-09-14

### Fixed
- The login page carried `Nova Hub v0.2.0` as a literal, which is where the
  version was read from and which had been wrong since 0.2.0. It is now baked in
  from `package.json` at build time -- the page cannot ask the API, being the
  page you see when you are not authenticated -- and a test fails if a version
  is hardcoded in a `.vue` file again, or if the frontend and backend versions
  drift apart.

## [0.3.0] - 2026-09-14

First tagged release since 0.1.0, and the version production is pinned to.
Thirty-five commits; the ones that change behaviour:

### Security
- Service tokens carry a `type` claim, so a management session token can no
  longer be presented to the service API or the reverse. Every token issued
  before the upgrade is rejected; clients re-authenticate on their next sync.
- Rate limiting counts failed authentication attempts only, so a healthy client
  cannot trip it.
- `min_password_length` and `cookie_secure` are stated rather than inherited.

### Added
- Two-tier retention. `retention_days` was configured, documented and never
  read for the life of the project; it now blanks aged transcripts and generated
  file bodies while keeping every row, count and filename. `VACUUM` stays an
  opt-in operator step.
- Out-of-band alerting via email or webhook, disabled until a transport is
  configured.
- `packets_unconsumed` on a processing run: packets copied into a game's inbound
  folder that the game never ingested are counted and left in place to be
  retried, instead of being deleted along with the evidence.
- A live test rig on real dosemu with real games, covering BRE and Falcon's Eye.
- `GET /management/api/v1/system/version` reports version, commit, commit date
  and whether the deployed tree is dirty. The admin sidebar shows it.
- City, state and country on clients, and the hub's per-league FidoNet address
  and routing mode on leagues, all editable in the UI.

### Fixed
- Generated nodelists contained no node 1 and no `HOST` routing line, because
  the hub has no client record to iterate. They are now written from config plus
  the league's own address, with CRLF endings, atomically, and generation
  refuses rather than overwriting a good file with a partial one.
- Dosemu failures were reported as successes: `script` masked the child's exit
  code, so a run that never started was recorded as completed.
- Sequence gap detection no longer raises false alerts on sparse routes or at
  wraparound.
- Multi-league processing attributed generated files to the wrong league.
- League deletion failed on foreign key constraints.

### Removed
- The WebSocket endpoints, which nothing in either the hub or the client used.


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
cp ./data/nova-hub.db ~/backups/

# Remove old database and migrations
rm ./data/nova-hub.db
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
   cp ./data/nova-hub.db ~/backups/nova-hub-$(date +%Y%m%d).db
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
