# Nova Hub

**Version:** 0.1.0


# Nova Hub - BBS Inter-League Routing System

Modern routing hub for Solar Realms games: Barren Realms Elite (BRE) and Falcons Eye (FE).

## Features

- **Packet Routing**: Hub-and-spoke routing for BBS door game leagues
- **OAuth Authentication**: Secure client authentication for packet transfer
- **Real-time Updates**: WebSocket support for live dashboard updates
- **Sequence Validation**: Automatic detection of missing packets
- **Web Interface**: Comprehensive sysop dashboard

## Authentication

API endpoints require OAuth 2.0 client credentials flow.

1. Obtain client credentials from hub administrator
2. Request token: `POST /auth/token` with `client_id` and `client_secret`
3. Use token in `Authorization: Bearer <token>` header

## Packet Naming

Packets follow the format: `<league><game><source><dest>.<seq>`

- **league**: 3-digit league number (e.g., 555)
- **game**: B (BRE) or F (FE)
- **source**: 2-digit hex BBS index (e.g., 02)
- **dest**: 2-digit hex BBS index (e.g., 01)
- **seq**: 3-digit sequence number (000-999, wraps)

Example: `555B0201.001` = BRE League 555, from BBS 02 to BBS 01, sequence 1

## WebSocket Events

Connect to `/ws/dashboard` for real-time updates:

- `packet_received` - New packet uploaded
- `processing_started` - Batch processing started
- `processing_complete` - Processing finished
- `stats_update` - Dashboard statistics updated
- `alert_created` - New sequence gap detected
    

## Contact

- **Name:** Nova Hub
- **URL:** https://github.com/yourusername/nova-hub

**License:** MIT

---

## Table of Contents

- [Authentication](#authentication)
- [Packets](#packets)
- [System](#system)
- [Web Interface](#web-interface)

## Authentication

### POST `/auth/token`

**Get Access Token**

OAuth2 token endpoint for client authentication

**Grant Type:** client_credentials

**Form Parameters:**
- `grant_type`: Must be "client_credentials"
- `client_id`: Your client ID
- `client_secret`: Your client secret

**Returns:** Access token valid for 24 hours

**Example:**
```bash
curl -X POST "https://hub.example.com/auth/token" \
  -d "grant_type=client_credentials" \
  -d "client_id=your_client_id" \
  -d "client_secret=your_secret"
```

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/auth/verify`

**Verify Access Token**

Verify your access token and get client information

**Requires:** Valid Bearer token in Authorization header

**Returns:** Client details if token is valid

**Example:**
```bash
curl -X GET "https://hub.example.com/auth/verify" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### Security

- **OAuth2PasswordBearer**

---

## Packets

### PUT `/api/v1/leagues/{league_id}/packets/{filename}`

**Upload Packet**

Upload a game packet to the hub using PUT with raw body

**Path Parameters:**
- `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)
- `filename`: Packet filename (e.g., "555B0201.001")

**Request Body:** Raw file data (application/octet-stream)

**Packet Filename Format:** `<league><game><source><dest>.<seq>`
- League: 3 digits (e.g., 555)
- Game: B (BRE) or F (FE)
- Source: 2-digit hex BBS index (e.g., 02)
- Dest: 2-digit hex BBS index (e.g., 01)
- Seq: 3-digit sequence (000-999)

**Example:** `555B0201.001` = BRE League 555, from BBS 02 to BBS 01, seq 1

**Authentication:** Requires Bearer token

**Example:**
```bash
curl -X PUT "https://hub.example.com/api/v1/leagues/555B/packets/555B0201.001" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary "@555B0201.001"
```

#### Parameters

**league_id**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^\d{3}[BF]$`

**filename**
  - Type: `string`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

#### Security

- **OAuth2PasswordBearer**

---

### GET `/api/v1/leagues/{league_id}/packets/{filename}`

**Download Packet**

Download a specific packet file

**Path Parameters:**
- `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)
- `filename`: Packet filename (e.g., "555B0102.001")

**Returns:** Binary packet file

**Description:**
Downloads a packet that is destined for your BBS. The packet is automatically
marked as downloaded after successful retrieval.

**Authentication:** Requires Bearer token and authorization for destination BBS

**Example:**
```bash
curl -X GET "https://hub.example.com/api/v1/leagues/555B/packets/555B0102.001" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -o 555B0102.001
```

#### Parameters

**league_id**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^\d{3}[BF]$`

**filename**
  - Type: `string`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

#### Security

- **OAuth2PasswordBearer**

---

### GET `/api/v1/leagues/{league_id}/packets`

**List Available Packets**

List packets available for download by this client

**Path Parameters:**
- `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)

**Query Parameters:**
- `unread`: Set to `true` to only show packets not yet downloaded (default: false)

**Returns:** List of packets destined for this client's BBS

**Authentication:** Requires Bearer token

**Example:**
```bash
# Get all packets
curl -X GET "https://hub.example.com/api/v1/leagues/555B/packets" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get only unread packets
curl -X GET "https://hub.example.com/api/v1/leagues/555B/packets?unread=true" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

#### Parameters

**league_id**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^\d{3}[BF]$`

**unread**
- Filter to only unread (not downloaded) packets
  - Type: `boolean`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

#### Security

- **OAuth2PasswordBearer**

---

### GET `/api/v1/leagues/{league_id}/nodelist`

**Download Nodelist**

Download the latest nodelist file for a league

**Path Parameters:**
- `league_id`: League identifier with game type (e.g., "555B" for BRE, "555F" for FE)

**Returns:** Binary nodelist file (BRNODES.xxx or FENODES.xxx)

**Description:**
The nodelist contains information about all BBS nodes in the league.
These files are generated by the hub and should be placed in your game directory.

**Authentication:** Requires Bearer token and league membership

**Example:**
```bash
curl -X GET "https://hub.example.com/api/v1/leagues/555B/nodelist" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -o BRNODES.555
```

#### Parameters

**league_id**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^\d{3}[BF]$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

#### Security

- **OAuth2PasswordBearer**

---

## System

### GET `/health`

**Health Check**

Health check endpoint for monitoring

Returns system status and statistics

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

## Web Interface

### GET `/login`

**Login Page**

Login page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/login`

**Login**

Process login

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/logout`

**Logout**

Logout

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/change-password`

**Change Password Form**

Change password form

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/change-password`

**Change Password**

Change password

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/`

**Root**

Redirect to dashboard

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/dashboard`

**Dashboard**

Dashboard page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/clients`

**Clients List**

Clients list page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/clients/{client_id}`

**Client Detail**

Client detail page

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/processing`

**Processing Runs**

Processing runs list page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/processing/{run_id}`

**Processing Run Detail**

Processing run detail page

#### Parameters

**run_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/alerts`

**Alerts Page**

Alerts page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/alerts/{alert_id}/resolve`

**Resolve Alert**

Mark alert as resolved

#### Parameters

**alert_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/processing/runs`

**Admin Processing Runs**

View processing runs and logs

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/admin/processing/trigger`

**Admin Trigger Processing**

Manually trigger packet processing

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/admin`

**Admin Page**

Admin home - redirect to users

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/admin/users`

**Admin Users**

Admin users page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/admin/users/new`

**Admin Users New**

New user form

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/admin/users/new`

**Admin Users Create**

Create new user

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/users/{user_id}/edit`

**Admin Users Edit**

Edit user form

#### Parameters

**user_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/users/{user_id}/edit`

**Admin Users Update**

Update user

#### Parameters

**user_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/users/{user_id}/delete`

**Admin Users Delete**

Delete user

#### Parameters

**user_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/clients`

**Admin Clients**

Admin clients page

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/admin/clients/new`

**Admin Clients New**

New client form

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/admin/clients/new`

**Admin Clients Create**

Create new client

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/clients/{client_id}/edit`

**Admin Clients Edit**

Edit client form

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/clients/{client_id}/edit`

**Admin Clients Update**

Update client

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/clients/{client_id}/config`

**Admin Clients Show Config**

Show client configuration

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/clients/{client_id}/regenerate-secret`

**Admin Clients Regenerate Secret**

Regenerate client secret

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/clients/{client_id}/delete`

**Admin Clients Delete**

Delete client

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/leagues`

**Admin Leagues List**

List all leagues

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### GET `/admin/leagues/new`

**Admin Leagues New**

New league form

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

---

### POST `/admin/leagues/new`

**Admin Leagues Create**

Create new league

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/leagues/{league_id}`

**Admin Leagues Detail**

League detail with membership management

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/leagues/{league_id}/edit`

**Admin Leagues Edit**

Edit league form

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/html`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/leagues/{league_id}/edit`

**Admin Leagues Update**

Update league

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/admin/leagues/{league_id}/delete`

**Admin Leagues Delete**

Delete league

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/leagues/{league_id}/members/add`

**Admin Leagues Add Member**

Add client to league

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/leagues/{league_id}/members/{client_id}/remove`

**Admin Leagues Remove Member**

Remove client from league

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**client_id**
  - Type: `integer`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/leagues/{league_id}/members/{membership_id}/update-bbs-index`

**Admin Leagues Update Member Bbs Index**

Update BBS index for a league membership

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**membership_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/admin/leagues/{league_id}/members/{membership_id}/update-fidonet`

**Admin Leagues Update Member Fidonet**

Update Fidonet address for a league membership

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**membership_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/x-www-form-urlencoded`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Security Schemes

### OAuth2PasswordBearer

**Type:** oauth2

**Flow:** password
- Token URL: `/auth/token`
- Scopes:
