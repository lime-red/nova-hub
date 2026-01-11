# Nova Hub - Complete Project Structure

```
nova-hub/
├── README.md                    # Project overview and quick start
├── SETUP_GUIDE.md              # Detailed setup instructions
├── PROJECT_STRUCTURE.md         # This file
├── requirements.txt             # Python dependencies
├── config.toml.example          # Configuration template
├── alembic.ini                  # Database migrations config
├── .env.example                 # Environment variables template
├── .gitignore                   # Git ignore rules
├── run.py                       # Server startup script
├── main.py                      # FastAPI application
│
├── app/                         # Main application package
│   ├── __init__.py
│   ├── database.py             # SQLAlchemy models and DB setup
│   ├── services/               # Business logic layer
│   │   ├── __init__.py
│   │   ├── packet_service.py  # Packet handling
│   │   ├── processing_service.py  # Game processing
│   │   ├── stats_service.py   # Statistics computation
│   │   ├── sequence_validator.py  # Gap detection
│   │   ├── dosemu_runner.py   # Dosemu integration
│   │   ├── watcher.py          # File system watching
│   │   └── websocket_service.py  # Real-time updates
│   └── models/                 # Pydantic models (if needed)
│       └── __init__.py
│
├── routers/                     # FastAPI route handlers
│   ├── __init__.py
│   ├── auth.py                 # OAuth authentication
│   ├── packets.py              # Packet API endpoints
│   ├── web.py                  # Web UI routes
│   └── websocket.py            # WebSocket endpoints
│
├── templates/                   # Jinja2 HTML templates
│   ├── base.html               # Base layout
│   ├── login.html              # Login page
│   ├── dashboard.html          # Main dashboard
│   ├── alerts.html             # Sequence alerts
│   ├── clients/
│   │   ├── list.html           # Client list
│   │   └── detail.html         # Client details
│   ├── processing/
│   │   ├── runs.html           # Processing history
│   │   └── run_detail.html    # Single run details
│   └── admin/
│       ├── users.html          # User management
│       ├── clients.html        # Client management
│       ├── user_form.html      # User form
│       └── client_form.html    # Client form
│
├── alembic/                     # Database migrations
│   ├── env.py                  # Alembic environment
│   ├── script.py.mako          # Migration template
│   └── versions/
│       └── 001_initial_schema.py  # Initial migration
│
├── static/                      # Static assets (CSS, JS, images)
│   └── (empty - using CDN resources)
│
├── client/                      # Client BBS application
│   ├── README.md               # Client documentation
│   ├── client.py               # Main client script
│   ├── config.toml.example     # Client configuration
│   ├── requirements.txt        # Client dependencies
│   └── .env.example            # Client environment vars
│
├── tests/                       # Test suite
│   ├── __init__.py
│   ├── test_integration.py    # Integration tests
│   ├── test_client.py         # Client tests
│   └── mock_hub.py            # Mock server for testing
│
└── data/                        # Runtime data (git-ignored)
    ├── nova-hub.db             # SQLite database
    ├── packets/
    │   ├── inbound/            # Incoming packets
    │   ├── outbound/           # Outgoing packets
    │   └── processed/          # Archived packets
    ├── dosemu/
    │   ├── bre/
    │   │   ├── inbound/
    │   │   └── outbound/
    │   └── fe/
    │       ├── inbound/
    │       └── outbound/
    └── logs/
        └── dosemu/             # Dosemu output logs
```

## Key Files Description

### Core Application

- **main.py**: FastAPI application instance, lifespan management
- **run.py**: Startup script with pretty output
- **app/database.py**: All SQLAlchemy models and database setup

### Services Layer

- **packet_service.py**: Packet filename parsing, validation
- **processing_service.py**: Main batch processing logic, Dosemu orchestration
- **stats_service.py**: Dashboard metrics, activity tracking
- **sequence_validator.py**: Detects missing packet sequences
- **dosemu_runner.py**: Handles Dosemu execution
- **watcher.py**: Monitors for hub-generated packets
- **websocket_service.py**: Real-time update broadcasting

### API Routes

- **auth.py**: OAuth2 token endpoint
- **packets.py**: POST/GET/LIST packet endpoints
- **web.py**: All web UI routes (login, dashboard, admin)
- **websocket.py**: WebSocket connection handlers

### Templates

All templates use Pico CSS framework:
- Dark theme by default
- Responsive design
- Minimal JavaScript (Alpine.js for interactivity)
- Chart.js for visualizations

### Client Application

- **client.py**: One-shot sync script (no daemon)
- Designed to be called from cron or batch scripts
- Logs metrics to JSON for monitoring

### Testing

- **mock_hub.py**: Standalone FastAPI server for testing
- **test_client.py**: Automated client test suite
- **test_integration.py**: Full integration tests

## Implementation Notes

All source code was provided in the conversation. To assemble:

1. Copy each code block from the conversation into the appropriate file
2. The conversation was structured chronologically:
   - First: Requirements and design
   - Second: Server implementation (hub)
   - Third: Client implementation  
   - Fourth: Database integration
   - Fifth: Complete integration with WebSocket

3. Each major section had complete, copy-pasteable code blocks

## Dependencies

### Server
- FastAPI: Web framework
- SQLAlchemy: ORM
- Alembic: Migrations
- Jinja2: Templates
- python-jose: JWT tokens
- passlib: Password hashing
- watchdog: File monitoring

### Client
- aiohttp: HTTP client
- python-dotenv: Environment vars
- toml: Configuration

## Configuration

### Server (config.toml)
- Server settings (host, port)
- Hub identification
- Processing settings
- Dosemu paths and commands
- Database location
- Security settings

### Client (client/config.toml)
- Hub URL
- OAuth credentials
- BBS information
- League configurations
- Sync settings

## Running

### Development
```bash
python run.py
```

### Production
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Client
```bash
python client/client.py
```

Or via cron:
```cron
*/5 * * * * cd /path/to/client && python client.py
```
