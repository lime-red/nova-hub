# Nova Hub - Code Reference

This document provides key code snippets and patterns used throughout the project.

## Database Models Pattern

```python
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()

class Client(Base):
    __tablename__ = "clients"
    
    id = Column(Integer, primary_key=True)
    client_id = Column(String, unique=True, nullable=False, index=True)
    bbs_name = Column(String, nullable=False)
    bbs_index = Column(String, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
```

## Packet Filename Parsing

```python
import re
from dataclasses import dataclass

PACKET_REGEX = re.compile(r'^(\d{3})([BF])([0-9A-F]{2})([0-9A-F]{2})\.(\d{3})$', re.IGNORECASE)

@dataclasses
class PacketInfo:
    league: str
    game: str  # 'B' or 'F'
    source: str
    dest: str
    sequence: int

def parse_packet_filename(filename: str) -> PacketInfo:
    match = PACKET_REGEX.match(filename)
    if not match:
        raise ValueError(f"Invalid packet filename: {filename}")
    
    league, game, source, dest, seq = match.groups()
    return PacketInfo(
        league=league,
        game=game.upper(),
        source=source.upper(),
        dest=dest.upper(),
        sequence=int(seq)
    )
```

## FastAPI Route Pattern

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db, Client

router = APIRouter()

@router.get("/api/example")
async def example_route(
    client: Client = Depends(verify_client_token),
    db: Session = Depends(get_db)
):
    # Your logic here
    return {"status": "success"}
```

## WebSocket Broadcast Pattern

```python
_connections: Set[WebSocket] = set()

async def broadcast(message: dict):
    message_json = json.dumps(message)
    disconnected = set()
    
    for ws in _connections:
        try:
            await ws.send_text(message_json)
        except Exception:
            disconnected.add(ws)
    
    for ws in disconnected:
        await disconnect(ws)
```

## Dosemu Execution Pattern

```python
async def run_game_process(self, game_type: str):
    cmd = [
        self.dosemu_path,
        "-f", str(dosemu_conf),
        "-dumb",
        "-E", "PROCESS.BAT"
    ]
    
    result = await asyncio.wait_for(
        self._run_command(cmd, output_log),
        timeout=self.timeout
    )
    
    return {
        "status": "success" if result.returncode == 0 else "error",
        "output": self._parse_dosemu_output(output_log)
    }
```

## Template Pattern (Jinja2)

```html
{% extends "base.html" %}

{% block content %}
<h1>{{ title }}</h1>

<article>
    <table role="grid">
        <thead>
            <tr>
                <th>Column</th>
            </tr>
        </thead>
        <tbody>
            {% for item in items %}
            <tr>
                <td>{{ item.name }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</article>
{% endblock %}
```

## Client Sync Pattern

```python
async def run(self):
    async with aiohttp.ClientSession() as session:
        self.session = session
        token = await self.get_token()
        self.token = token
        
        for game_type, leagues in self.config['leagues'].items():
            for league_number, league_config in leagues.items():
                await self.sync_league(game_type, league_number, league_config)
```

## Testing Pattern

```python
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    # Setup test database
    yield TestClient(app)
    # Teardown

def test_example(client):
    response = client.get("/health")
    assert response.status_code == 200
```

## Key Patterns Summary

1. **Database**: SQLAlchemy with declarative base
2. **API**: FastAPI with dependency injection
3. **Auth**: OAuth2 with JWT tokens
4. **Processing**: Async with locks
5. **WebSocket**: Connection set with broadcast
6. **Templates**: Jinja2 with Pico CSS
7. **Client**: Aiohttp one-shot execution
8. **Testing**: Pytest with fixtures

For complete implementations, see the conversation history where each
component was fully specified with working code.
