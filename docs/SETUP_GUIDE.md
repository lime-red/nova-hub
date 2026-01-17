# Nova Hub - Setup Guide

Complete setup instructions for deploying Nova Hub.

## Prerequisites

- Python 3.12 or higher
- `uv` package manager (recommended) or `pip`
- SQLite3 (for database)
- Linux/Unix environment (for Dosemu integration)

## Directory Structure

Nova Hub follows a separation of code and data:

```
/home/lime/nova-hub/     # Code directory
/home/lime/nova-data/    # Runtime data directory
```

This allows you to update code without affecting production data.

## Installation Steps

### 1. Install Dependencies

Using `uv` (recommended):
```bash
cd /home/lime/nova-hub
uv venv
source .venv/bin/activate  # or use uv directly
uv pip install -r requirements.txt
```

Using `pip`:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Database Path

Edit `config.toml` and `alembic.ini` to set your data directory:

**config.toml:**
```toml
[database]
path = "/home/lime/nova-data/nova-hub.db"
```

**alembic.ini:**
```ini
sqlalchemy.url = sqlite:////home/lime/nova-data/nova-hub.db
```

Also update the default `DATABASE_URL` in `app/database.py` to match.

### 3. Create Data Directory

```bash
mkdir -p /home/lime/nova-data
```

### 4. Initialize Database

Run Alembic migrations to create all database tables:

```bash
.venv/bin/alembic upgrade head
```

This creates:
- `clients` - OAuth clients (remote BBS nodes)
- `leagues` - Game leagues (BRE/FE)
- `league_memberships` - Client-to-league associations
- `sysop_users` - Web UI authentication
- `packets` - Uploaded game packets
- `processing_runs` - Dosemu batch processing runs
- `sequence_alerts` - Missing packet alerts
- `system_settings` - System configuration

### 5. Create Default Admin User

Run the admin creation script:

```bash
.venv/bin/python create_admin_sql.py
```

This creates a default admin user with:
- **Username:** `admin`
- **Password:** `admin`

⚠️ **IMPORTANT:** Change this password immediately after first login!

### 6. Configure Hub Settings

Edit `config.toml` for your hub:

```toml
[server]
host = "0.0.0.0"
port = 8000
data_dir = "/home/lime/nova-data"

[hub]
bbs_name = "Your Hub Name"
bbs_index = "01"  # Hex index for your hub

[security]
jwt_secret = "change-me-in-production"  # Generate a secure random key
jwt_expiry_hours = 24
```

### 7. Start the Server

```bash
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

For development with auto-reload:
```bash
.venv/bin/uvicorn main:app --reload
```

### 8. Access Web Interface

Open your browser and navigate to:
```
http://localhost:8000
```

You'll be redirected to the login page. Use the admin credentials from step 5.

## Post-Installation

### Change Admin Password

1. Log in as admin
2. Go to Admin > Users
3. Create a new admin user with a secure password
4. Log out and log in with the new account
5. Delete or disable the default admin account

### Register BBS Clients

1. Go to Admin > Clients
2. Click "New Client"
3. Fill in:
   - BBS Name
   - BBS Index (unique 2-digit hex: 01-FF)
   - Client ID (used for OAuth)
4. Save and securely share the generated client secret with the BBS operator

### Configure Leagues

1. Leagues are automatically created when the first packet is uploaded
2. Or manually create via the database/admin interface
3. Configure Dosemu paths for each league if using batch processing

## Client Setup

On each BBS node, configure the client application:

**client/config.toml:**
```toml
[hub]
base_url = "https://your-hub-domain.com"
client_id = "your-client-id"
client_secret = "your-client-secret"

[local]
bbs_index = "02"  # This BBS's index
packet_dir = "/path/to/game/packets"
```

Run the client:
```bash
python client/client.py
```

Or set up a cron job for automatic syncing:
```bash
*/5 * * * * cd /path/to/client && python client.py
```

## Database Management

### View Database

```bash
sqlite3 /home/lime/nova-data/nova-hub.db
```

### Backup Database

```bash
cp /home/lime/nova-data/nova-hub.db /path/to/backups/nova-hub-$(date +%Y%m%d).db
```

### Create New Migration

After modifying models in `app/database.py`:

```bash
.venv/bin/alembic revision --autogenerate -m "Description of changes"
.venv/bin/alembic upgrade head
```

## Troubleshooting

### Database Errors

If you get database connection errors:
1. Check that the data directory exists
2. Verify paths in `config.toml`, `alembic.ini`, and `app/database.py` match
3. Ensure the database file has proper permissions

### Login Issues

If login fails with valid credentials:
1. Verify user exists: `sqlite3 /home/lime/nova-data/nova-hub.db "SELECT * FROM sysop_users;"`
2. Check that bcrypt is properly installed
3. Recreate the admin user with `create_admin_sql.py`

### Import Errors

If you get import errors when starting:
1. Ensure all dependencies are installed: `uv pip install -r requirements.txt`
2. Check that you're using the virtual environment
3. Verify all required service modules exist in `app/services/`

### Alembic "env.py not found"

If Alembic can't find env.py:
1. Check that `alembic/env.py` exists
2. Verify `alembic/script.py.mako` exists
3. Check the `script_location` in `alembic.ini`

## Security Considerations

1. **Change Default Credentials:** Never use default admin credentials in production
2. **Secure JWT Secret:** Generate a strong random key for `jwt_secret` in config.toml
3. **HTTPS:** Use a reverse proxy (nginx/caddy) with SSL certificates for production
4. **Firewall:** Restrict access to port 8000 if running behind a proxy
5. **Client Secrets:** Keep client secrets secure and rotate them periodically
6. **Database Backups:** Implement regular automated backups

## Production Deployment

For production, use a process manager:

**systemd service** (`/etc/systemd/system/nova-hub.service`):
```ini
[Unit]
Description=Nova Hub BBS Router
After=network.target

[Service]
Type=simple
User=lime
WorkingDirectory=/home/lime/nova-hub
Environment="PATH=/home/lime/nova-hub/.venv/bin"
ExecStart=/home/lime/nova-hub/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable nova-hub
sudo systemctl start nova-hub
```

## API Documentation

Once running, access API documentation at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Support

For issues or questions:
1. Check the logs: `journalctl -u nova-hub -f`
2. Review the error messages
3. Check database integrity
4. Verify configuration files

## Architecture Overview

### Database Models

- **SysopUser** - Web UI authentication
- **Client** - OAuth clients (remote BBS nodes)
- **League** - Game leagues (BRE/FE)
- **LeagueMembership** - Client-to-league associations
- **Packet** - Uploaded game packets
- **ProcessingRun** - Dosemu batch processing runs
- **SequenceAlert** - Missing packet alerts
- **SystemSettings** - System configuration

### API Routes

- `POST /auth/token` - OAuth token endpoint
- `POST /api/v1/leagues/{league_id}/packets` - Upload packet
- `GET /api/v1/leagues/{league_id}/packets` - List pending packets
- `GET /api/v1/packets/{filename}` - Download packet
- `/ws/dashboard` - WebSocket for real-time updates

### Web Interface

- `/` - Dashboard with statistics and charts
- `/clients` - View registered BBS clients
- `/processing` - View processing run history
- `/alerts` - View sequence gap alerts
- `/admin/users` - Manage sysop users
- `/admin/clients` - Manage BBS clients
