# Nova Hub - Quick Start

Get Nova Hub running in 5 minutes.

## Prerequisites

- Python 3.12+
- `uv` or `pip`
- SQLite3

## Installation

```bash
# 1. Clone/navigate to the project
cd /home/lime/nova-hub

# 2. Create virtual environment and install dependencies
uv venv
uv pip install -r requirements.txt

# 3. Create data directory
mkdir -p /home/lime/nova-data

# 4. Initialize database
.venv/bin/alembic upgrade head

# 5. Create admin user
.venv/bin/python create_admin_sql.py

# 6. Start server
.venv/bin/uvicorn main:app --reload
```

## First Login

1. Open http://localhost:8000
2. Login with:
   - Username: `admin`
   - Password: `admin`
3. **Change the password immediately!**

## Next Steps

1. **Create BBS Clients:**
   - Go to Admin > Clients
   - Add each BBS node with unique index (01-FF)
   - Save the generated client secret

2. **Configure Client Nodes:**
   - Install client.py on each BBS
   - Configure with hub URL and credentials
   - Set up cron job for automatic sync

3. **Monitor Activity:**
   - Dashboard shows real-time statistics
   - View packets, processing runs, and alerts
   - WebSocket provides live updates

## Common Commands

```bash
# Start server (development)
.venv/bin/uvicorn main:app --reload

# Start server (production)
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

# Run migrations
.venv/bin/alembic upgrade head

# Create new migration
.venv/bin/alembic revision --autogenerate -m "Description"

# View database
sqlite3 /home/lime/nova-data/nova-hub.db

# Backup database
cp /home/lime/nova-data/nova-hub.db ~/backups/nova-hub-$(date +%Y%m%d).db
```

## File Locations

- **Code:** `/home/lime/nova-hub/`
- **Data:** `/home/lime/nova-data/`
- **Database:** `/home/lime/nova-data/nova-hub.db`
- **Config:** `/home/lime/nova-hub/config.toml`
- **Logs:** Check systemd journal or uvicorn output

## Troubleshooting

**Can't login?**
```bash
# Verify admin user exists
sqlite3 /home/lime/nova-data/nova-hub.db "SELECT username FROM sysop_users;"

# Recreate if needed
.venv/bin/python create_admin_sql.py
```

**Database errors?**
```bash
# Check paths match in these files:
# - config.toml [database.path]
# - alembic.ini [sqlalchemy.url]
# - app/database.py [DATABASE_URL]

# Verify data directory exists
ls -la /home/lime/nova-data/
```

**Import errors?**
```bash
# Reinstall dependencies
uv pip install -r requirements.txt

# Check virtual environment is activated
which python  # Should show .venv path
```

## Production Setup

Use systemd for production:

```bash
# Create service file
sudo nano /etc/systemd/system/nova-hub.service

# Enable and start
sudo systemctl enable nova-hub
sudo systemctl start nova-hub
sudo systemctl status nova-hub
```

Add nginx/caddy for HTTPS:
```nginx
server {
    listen 443 ssl;
    server_name hub.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## More Information

- Full setup guide: `docs/SETUP_GUIDE.md`
- Project structure: `docs/PROJECT_STRUCTURE.md`
- API reference: http://localhost:8000/docs
