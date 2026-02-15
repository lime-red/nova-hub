# Nova Hub

Modern routing hub and spoke router for Solar Realms games: Barren Realms Elite (BRE) and Falcons Eye (FE)

## Features

- 🌐 **Hub-and-Spoke Routing**: Centralized packet routing for multiple BBS nodes
- 🔐 **OAuth Authentication**: Secure client credentials flow for API access
- 📊 **Web Dashboard**: Real-time monitoring and statistics
- 🔄 **Real-time Updates**: WebSocket support for live notifications
- ⚠️ **Sequence Validation**: Automatic detection of missing packets
- 🎮 **Multi-Game Support**: Handles both BRE and FE leagues
- 📦 **Dosemu Integration**: Processes packets via Dosemu

## Quick Start

For detailed instructions, see [docs/QUICKSTART.md](docs/QUICKSTART.md)

### Hub Server

```bash
# Install dependencies
uv pip install -r requirements.txt

# Create data directory
mkdir -p ./data

# Initialize database
.venv/bin/alembic upgrade head

# Create admin user
.venv/bin/python create_admin_sql.py

# Start server
.venv/bin/uvicorn main:app --reload
```

Access the web interface at: http://localhost:8000

Default credentials:
- Username: `admin`
- Password: `admin` (⚠️ Change this immediately!)

### Client Setup

See the [nova-client](../nova-client/) directory for the full client with daemon support.

```bash
cd ../nova-client

# Install dependencies
pip install -r requirements.txt

# Configure client
cp config.toml.example config.toml
# Edit with your client credentials from hub admin

# Test run
python client.py

# Or run as daemon for continuous operation
python daemon.py
```

## Architecture

```
┌─────────────┐
│   Clients   │ (Multiple BBSs)
└──────┬──────┘
       │ HTTPS/OAuth
       ↓
┌─────────────────────────────┐
│      Nova Hub Server        │
│  ┌─────────────────────┐    │
│  │  Web UI (Dashboard) │    │
│  └─────────────────────┘    │
│  ┌─────────────────────┐    │
│  │  Packet API         │    │
│  └─────────────────────┘    │
│  ┌─────────────────────┐    │
│  │  Dosemu Processing  │    │
│  └─────────────────────┘    │
└─────────────────────────────┘
       │
       ↓
   SQLite DB
```

## Documentation

- **[Quick Start Guide](docs/QUICKSTART.md)** - Get up and running in 5 minutes
- **[Setup Guide](docs/SETUP_GUIDE.md)** - Comprehensive installation and configuration
- **[Project Structure](docs/PROJECT_STRUCTURE.md)** - Code organization and architecture
- **[Code Reference](docs/CODE_REFERENCE.md)** - Developer reference
- **API Documentation** - Once running:
  - Service API (for clients): http://localhost:8000/service/docs
  - Management API (for admin): http://localhost:8000/management/docs

## Packet Format

Packets follow the naming convention: `<league><game><source><dest>.<seq>`

Example: `555B0201.001`
- League: `555` (BRE League 555)
- Game: `B` (BRE) or `F` (FE)
- Source BBS: `02` (hex)
- Destination BBS: `01` (hex)
- Sequence: `001` (000-999, wraps)

## Testing

```bash
# Run hub tests
.venv/bin/pytest tests/

# Run mock hub for client testing
.venv/bin/python tests/mock_hub.py

# In another terminal, run client tests
cd ../nova-client && pytest tests/
```

## Configuration

### Hub Server (config.toml)

```toml
[server]
host = "0.0.0.0"
port = 8000

[hub]
bbs_name = "My Hub"
bbs_index = "01"

[database]
path = "./data/nova-hub.db"

[dosemu]
dosemu_path = "/usr/bin/dosemu"
# ... see config.toml.example for full options
```

### Client (nova-client/config.toml)

```toml
[hub]
url = "https://hub.example.com"
client_id = "your_client_id"
client_secret = "your_secret"

[bbs]
name = "My BBS"

[leagues.BRE.555]
enabled = true
bbs_index = 2                           # Your BBS ID for this league (1-255)
outbound_dir = "/path/to/bre/outbound"
inbound_dir = "/path/to/bre/inbound"
```

## Database Migrations

```bash
# Create new migration after model changes
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Deployment

### Systemd Service (Hub)

```bash
# See deploy/ directory for Ansible playbook and service template
sudo cp deploy/nova-hub.service.j2 /etc/systemd/system/nova-hub.service  # edit paths first
sudo systemctl enable nova-hub
sudo systemctl start nova-hub
```

### Reverse Proxy (Caddy)

```
hub.example.com {
    reverse_proxy localhost:8000
}
```

## Troubleshooting

### Authentication Issues

If you can't create admin user or login fails:
```bash
# Use the SQL-based admin creation script
.venv/bin/python create_admin_sql.py

# Or verify user exists
sqlite3 ./data/nova-hub.db "SELECT * FROM sysop_users;"
```

### Dosemu Issues

1. Ensure Dosemu 2.x is installed
2. Check file permissions on dosemu directories
3. Verify game executables are in correct paths
4. Check logs in `data/logs/dosemu/`

### Client Connection Issues

1. Verify hub URL is correct
2. Check client credentials
3. Ensure firewall allows outbound HTTPS
4. Check client logs for errors

### Sequence Gaps

Sequence gaps can occur due to:
- Network issues during upload
- Client crashes mid-transfer
- Hub processing errors

Check the Alerts page in the web UI for details.

## License

MIT License - See LICENSE file

## Credits

Created for the BBS community to facilitate Inter-BBS leagues for:
- Barren Realms Elite (BRE) by Mehul Patel
- Falcons Eye (FE) by Mehul Patel

## Support

For issues or questions, please open an issue on GitHub.
