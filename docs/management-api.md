# Nova Hub - Management API

**Version:** 0.9.0


# Management API for Sysop Administration

This API powers the Nova Hub admin dashboard and provides:

- **Session Authentication** using JWT cookies
- **Dashboard** statistics and activity feed (polled via REST)
- **Client Management** - CRUD operations for BBS clients
- **League Management** - Leagues and membership configuration
- **Processing Runs** - View processing history and logs
- **Alerts** - Sequence gap detection and resolution
- **User Management** - Admin user CRUD (admin only)

## Authentication

Uses session-based JWT stored in httpOnly cookies.

1. Login: `POST /api/v1/auth/login` with username/password
2. Cookie automatically included in subsequent requests
3. Logout: `POST /api/v1/auth/logout`
    

**License:** MIT

---

## Table of Contents

- [Management API - Address Book](#management-api---address-book)
- [Management API - Alerts](#management-api---alerts)
- [Management API - Applications](#management-api---applications)
- [Management API - Attacks](#management-api---attacks)
- [Management API - Audit Log](#management-api---audit-log)
- [Management API - Authentication](#management-api---authentication)
- [Management API - Claim Links](#management-api---claim-links)
- [Management API - Clients](#management-api---clients)
- [Management API - Dashboard](#management-api---dashboard)
- [Management API - Game Reset](#management-api---game-reset)
- [Management API - Leagues](#management-api---leagues)
- [Management API - Movements](#management-api---movements)
- [Management API - Processing](#management-api---processing)
- [Management API - System](#management-api---system)
- [Management API - Traffic](#management-api---traffic)
- [Management API - Users](#management-api---users)

## Management API - Address Book

### GET `/management/api/v1/address-book`

**Address Book**

Every BBS against every active league: its index and FTN address in each,
and whether its index differs between leagues.

Only active memberships are listed. BBSes in no league are included, so a
newly added one shows up here before it joins anything.

A sysop sees only their own leagues, the BBSes in them, and the addresses
those BBSes use there -- what the leagues' nodelists publish.

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

## Management API - Alerts

### GET `/management/api/v1/alerts`

**List All Alerts**

List all alerts (sequence gaps)

**Query Parameters:**
- `resolved`: Filter by resolved status (omit for all, true/false for filtered)
- `limit`: Number of alerts to return (default: 100, max: 500)

**Returns:** List of alerts ordered by unresolved first, then by date

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/alerts?resolved=false" \
  -b cookies.txt
```

#### Parameters

**resolved**
- Filter by resolved status (true/false/none for all)
  - Type: `boolean`
  - Location: query

**limit**
- Number of alerts to return
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/alerts/{alert_id}`

**Get Alert Details**

Get details about a specific alert

**Path Parameters:**
- `alert_id`: Database ID of the alert

**Returns:** Alert details

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/alerts/1" \
  -b cookies.txt
```

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

### POST `/management/api/v1/alerts/{alert_id}/resolve`

**Resolve Alert**

Mark an alert as resolved (admin only)

**Path Parameters:**
- `alert_id`: Database ID of the alert

**Returns:** Success message

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/alerts/1/resolve" \
  -b cookies.txt
```

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

### POST `/management/api/v1/alerts/{alert_id}/unresolve`

**Unresolve Alert**

Mark an alert as unresolved (admin only)

**Path Parameters:**
- `alert_id`: Database ID of the alert

**Returns:** Success message

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/alerts/1/unresolve" \
  -b cookies.txt
```

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

## Management API - Applications

### GET `/management/api/v1/applications/leagues`

**Leagues Open to Join**

The leagues a sysop can ask to join: names and games only.

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/management/api/v1/applications/mine`

**My Applications**

The signed-in user's own applications, newest first.

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### POST `/management/api/v1/applications`

**Apply**

Apply to join with a new BBS (`kind: "join"`), or ask for more leagues for
a BBS you run (`kind: "leagues"`). 409, with the reason, if it can't be
accepted: one waiting join per account, one waiting request per BBS.

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/applications`

**Application Queue**

Applications, oldest waiting first; decided ones newest first (admin only).

#### Parameters

**status**
- pending, approved, rejected, withdrawn, or all
  - Type: `string`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/applications/{application_id}/withdraw`

**Withdraw Application**

Withdraw one of your own applications while it is waiting.

#### Parameters

**application_id**
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

### GET `/management/api/v1/applications/{application_id}`

**Review Application**

One application, with suggested indexes and addresses for approving it (admin only).

#### Parameters

**application_id**
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

### POST `/management/api/v1/applications/{application_id}/approve`

**Approve Application**

Approve, granting each league with the BBS index and FTN address given (admin only).

A join creates the BBS, makes the applicant its owner, and returns a claim
link bound to the applicant: only they, signed in, can use it, and it
also waits for them in the console. The leagues' nodelists are
regenerated; `nodelist_problems` lists any that could not be, which leaves
the approval standing. 409 with the reason if it can't be approved as given.

#### Parameters

**application_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/applications/{application_id}/reject`

**Reject Application**

Reject, with a note the sysop sees the next time they sign in (admin only).

#### Parameters

**application_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Attacks

### GET `/management/api/v1/attacks/`

**List Attack Journeys**

Individual attacks, most recently launched first, each with its hops.

**Query Parameters:**
- `days`: launched within this many days (default 14)
- `league_id`, `planet`: filters; `planet` matches either side
- `attack_id`: find one attack by ID, whatever its age
- `stage`: only attacks whose journey stopped at this stage
- `mit`: `late` (result handed over after the attacker's board rolled into the
  due date, so discarded), `possible` (handed over while it rolled over, or no
  rollover seen yet), `overdue` (past the due date, no result delivered), or `any`

#### Parameters

**days**
  - Type: `integer`
  - Location: query

**league_id**
  - Type: `string`
  - Location: query

**planet**
- Attacker's or target's planet
  - Type: `string`
  - Location: query

**attack_id**
- ID or a prefix of it; ignores `days`
  - Type: `string`
  - Location: query

**stage**
  - Type: `string`
  - Location: query

**mit**
  - Type: `string`
  - Location: query

**limit**
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/attacks/{attack_id}/forces`

**Reveal an Attack's Forces (admin)**

Forces sent, and once resolved, whether it won, the losses, and regions taken.

Hidden game state, so admin only, one attack per request, and never part of
the listing: the UI fetches it only when the admin clicks to reveal. Each
reveal is logged.

#### Parameters

**attack_id**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^[0-9a-fA-F]{16}$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Audit Log

### GET `/management/api/v1/audit`

**Audit Log**

Who did what, newest first (admin only)

**Example:** everything that happened to one BBS
```bash
curl "https://hub.example.com/management/api/v1/audit?target_type=client&target_id=3" \
  -b cookies.txt
```

#### Parameters

**target_type**
- client or user
  - Type: `string`
  - Location: query

**target_id**
  - Type: `string`
  - Location: query

**action**
- An action, or a prefix such as 'owner.'
  - Type: `string`
  - Location: query

**before_id**
- Page back: events older than this id
  - Type: `string`
  - Location: query

**limit**
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Authentication

### POST `/management/api/v1/auth/login`

**Login**

Authenticate user and set session cookie

**Request Body:**
- `username`: Sysop username
- `password`: Sysop password

**Returns:** User info and sets httpOnly session cookie

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}' \
  -c cookies.txt
```

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/auth/logout`

**Logout**

Clear session cookie

**Returns:** Success message

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/auth/logout" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/management/api/v1/auth/me`

**Get Current User**

Get current authenticated user info

**Requires:** Valid session cookie

**Returns:** Current user details

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/auth/me" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### POST `/management/api/v1/auth/change-password`

**Change Password**

Change current user's password

**Request Body:**
- `current_password`: Current password
- `new_password`: New password
- `confirm_password`: Confirm new password

**Returns:** Success message

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/auth/change-password" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "old", "new_password": "new", "confirm_password": "new"}' \
  -b cookies.txt
```

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/auth/methods`

**Sign-in Methods**

Which sign-in methods the login page should offer. Public.

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/management/api/v1/auth/sso/start`

**Start Provider Sign-in**

Send the browser to the identity provider to sign in.

A random state goes both into the provider URL and, signed, into a short-lived
cookie; the callback accepts only a state that matches its own browser's
cookie, so a sign-in cannot be started in one browser and finished in another.

#### Parameters

**next**
- Console path to land on afterwards
  - Type: `string`
  - Location: query

**relink**
- A re-link link's token: bind this sign-in to the account it was issued for
  - Type: `string`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Claim Links

### GET `/management/api/v1/claim/{token}`

**Claim Link Status**

What a claim link is for and whether it can still be used. Changes nothing.

**Returns:** the BBS's name, the link's state (`ready`, `used`, `superseded`
or `expired`), whether claiming needs a sign-in and whether the viewer is
the right account, its expiry, and when and from where it was used if it was.

#### Parameters

**token**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^[A-Za-z0-9_-]{20,64}$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/claim/{token}`

**Claim Credentials**

Use a claim link: generate the BBS's client secret and return it, once.

The new secret replaces the BBS's previous one immediately. Returns the
credentials together with ready-made client config files (Linux
`config.toml`, Windows `config.psd1`) listing the BBS's leagues. 409 if
the link has been used, superseded or has expired. A bound link needs its
account signed in: 401 when no one is, 403 when someone else is.

#### Parameters

**token**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^[A-Za-z0-9_-]{20,64}$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/relink/{token}`

**Re-link Link Status**

Which hub account this link connects a sign-in to, and whether it still can.

#### Parameters

**token**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^[A-Za-z0-9_-]{20,64}$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Clients

### GET `/management/api/v1/clients`

**List All Clients**

List clients with basic stats: all of them for an admin, a sysop's own for a sysop

**Returns:** List of clients with 24h activity stats

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/clients" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### POST `/management/api/v1/clients`

**Create Client**

Create a new client (admin only)

**Request Body:**
- `bbs_name`: Display name for the BBS
- `client_id`: OAuth2 client ID (must be unique)
- `city`, `state`, `country`: location lines for generated nodelists
  (optional)

**Returns:** Created client with plain-text secret (shown only once)

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/clients" \
  -H "Content-Type: application/json" \
  -d '{"bbs_name": "Test BBS", "client_id": "test_client"}' \
  -b cookies.txt
```

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/clients/{client_id}`

**Get Client Details**

Get detailed information about a specific client

**Path Parameters:**
- `client_id`: Database ID of the client

**Returns:** Client details with stats and recent packets

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/clients/1" \
  -b cookies.txt
```

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

### PUT `/management/api/v1/clients/{client_id}`

**Update Client**

Update a client (admin only)

**Path Parameters:**
- `client_id`: Database ID of the client

**Request Body:**
- `bbs_name`: New display name (optional)
- `city`, `state`, `country`: location lines for generated nodelists
  (optional)
- `is_active`: Active status (optional)

**Returns:** Updated client

**Example:**
```bash
curl -X PUT "https://hub.example.com/management/api/v1/clients/1" \
  -H "Content-Type: application/json" \
  -d '{"bbs_name": "Updated BBS", "is_active": false}' \
  -b cookies.txt
```

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/clients/{client_id}`

**Delete Client**

Delete a client (admin only)

**Path Parameters:**
- `client_id`: Database ID of the client

**Returns:** Success message

**Example:**
```bash
curl -X DELETE "https://hub.example.com/management/api/v1/clients/1" \
  -b cookies.txt
```

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

### POST `/management/api/v1/clients/{client_id}/claim-link`

**Issue Claim Link**

Issue a single-use link that gives this BBS's sysop its credentials (admin only).

Send the link instead of a secret. It expires after 72 hours and supersedes
any outstanding link for this BBS. Nothing changes for the BBS until the
link is claimed; claiming generates a new secret, which replaces the
current one.

**Request Body (optional):** `user_id` binds the link to one of the BBS's
owners: only that account, signed in, can claim it, and it waits for them
in the console too. Without it the link is unbound, for a sysop with no
account.

**Returns:** the link (shown only here) and its expiry

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/clients/{client_id}/claim-link`

**Claim Link Status**

The latest claim link issued for this BBS, or null (admin only).

Never includes the link itself, which exists only in the response that
issued it.

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

### POST `/management/api/v1/clients/{client_id}/claim-link/collect`

**Collect Claim Link**

The claim link waiting for the signed-in sysop, as a link they can open.

Only the link's hash is kept, so the one the admin was shown can't be
shown again: this replaces it with a new one, bound to the same account
and expiring at the same time, and returns that. 404 when no link is
waiting for this account -- a sysop can't rotate their BBS's secret this
way, only collect what the admin issued.

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

### POST `/management/api/v1/clients/{client_id}/regenerate-secret`

**Regenerate Secret**

Regenerate client OAuth2 secret (an admin, or a sysop who owns the BBS)

With no body, or `overlap_hours` 0, the old secret stops now, and so do
the access tokens already issued with it. With `overlap_hours` (up to 72)
the old secret keeps working that long, so the node isn't cut off before
its config is edited.

**Path Parameters:**
- `client_id`: Database ID of the client

**Returns:** New client secret (shown only once), and when the old one stops

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/clients/1/regenerate-secret" \
  -H "Content-Type: application/json" -d '{"overlap_hours": 24}' \
  -b cookies.txt
```

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/clients/{client_id}/config/{platform}`

**Download Sample Client Config**

Download a sample client config for this BBS: the file a claim hands back,
without the secret.

The hub keeps only the secret's hash, so `client_secret` is a CHANGE-ME
placeholder, like the game paths. The hub address, client ID, BBS name and
current leagues with their BBS indexes are filled in.

**Path Parameters:**
- `client_id`: Database ID of the client
- `platform`: `linux` (`config.toml`, Python client) or `windows`
  (`config.psd1`, PowerShell client)

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

**platform**
  - Type: `string`
  - Location: path *(required)*

#### Responses

#### 200

Successful Response


**Content-Type:** `text/plain`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/clients/{client_id}/ftn-addresses`

**Assign FTN Address**

Assign an FTN address to a BBS (admin only)

The address must not belong to any other BBS. Use it in a league by
choosing it on that league's membership.

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/clients/1/ftn-addresses" \
  -H "Content-Type: application/json" \
  -d '{"address": "135:135/21"}' \
  -b cookies.txt
```

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### PUT `/management/api/v1/clients/{client_id}/ftn-addresses/{address_id}`

**Renumber FTN Address**

Change an FTN address (admin only)

Every league membership using it follows, and each of those leagues'
nodelists picks the change up at its next regeneration.

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

**address_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/clients/{client_id}/ftn-addresses/{address_id}`

**Remove FTN Address**

Remove an FTN address from a BBS (admin only)

Refused while any league membership still uses it.

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

**address_id**
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

### POST `/management/api/v1/clients/{client_id}/owners`

**Add Owner**

Make a sysop an owner of this BBS (admin only)

The sysop then sees the BBS, its leagues, packets and runs in the console.
A BBS can have several owners, and a sysop can own several BBSes.

**Returns:** the BBS's owners

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/clients/{client_id}/owners/{user_id}`

**Remove Owner**

Stop a sysop owning this BBS (admin only)

**Returns:** the BBS's remaining owners

#### Parameters

**client_id**
  - Type: `integer`
  - Location: path *(required)*

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

## Management API - Dashboard

### GET `/management/api/v1/dashboard/stats`

**Get Dashboard Stats**

Get dashboard statistics

**Returns:** Core statistics for dashboard cards

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard/stats" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/management/api/v1/dashboard/activity`

**Get Recent Activity**

Get recent activity feed

**Query Parameters:**
- `limit`: Number of items to return (default: 10, max: 100)

**Returns:** List of recent activity items

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard/activity?limit=20" \
  -b cookies.txt
```

#### Parameters

**limit**
- Number of items to return
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/dashboard/alerts`

**Get Active Alerts**

Get active (unresolved) alerts for dashboard

**Query Parameters:**
- `limit`: Number of alerts to return (default: 5, max: 50)

**Returns:** List of active alerts

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard/alerts?limit=10" \
  -b cookies.txt
```

#### Parameters

**limit**
- Number of alerts to return
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/dashboard/charts/activity`

**Get Activity Chart Data**

Get activity chart data (packets per hour)

**Query Parameters:**
- `hours`: Number of hours to include (default: 24, max: 168 = 1 week)

**Returns:** Chart labels and data arrays

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard/charts/activity?hours=48" \
  -b cookies.txt
```

#### Parameters

**hours**
- Number of hours to include
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/dashboard/charts/leagues`

**Get League Distribution Chart**

Get league distribution chart data (packets by league)

**Returns:** Chart labels (league names) and data (packet counts)

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard/charts/leagues" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### GET `/management/api/v1/dashboard`

**Get Full Dashboard Data**

Get all dashboard data in a single request

**Returns:** Stats, activity, alerts, and chart data

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/dashboard" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

## Management API - Game Reset

### GET `/management/api/v1/leagues/{league_id}/reset`

**Get the Game Reset Form**

Every setting the game's reset editor offers, in the editor's order, with
the game's own help, its default and our limits; the league's draft, if any;
the settings of the last reset performed from the console; and what the hub
last decoded from the league's Configupdate.

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

### PUT `/management/api/v1/leagues/{league_id}/reset/draft`

**Save the Game Reset Draft**

Check every setting against the game's limits and save the league's draft.

A value below our advised minimum is saved, and listed in the draft's
`warnings`.

Answers 422 listing each field that fails, and 409 when someone else saved the
draft since `version` was loaded. Fields left out take the game's defaults.

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/leagues/{league_id}/reset/draft`

**Discard the Game Reset Draft**

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

### POST `/management/api/v1/leagues/{league_id}/reset/perform`

**Perform the Game Reset**

Reset the league's game with its draft. **Every player on every board in
the league is wiped** once members run the Game Start this sends.

The draft must be at `version`, and `confirm` must be the league's code. The
hub then backs up the game, types the draft into the game's own reset
editor, checks every setting on both pages, and saves it as a league-wide
reset; a PLANETARY run then sends the Game Start. Anything unexpected before
the save puts the game back as it was and returns the settings to the draft.

Answers 202 at once with the reset under way; follow it with
`GET /leagues/{id}/resets/{reset_id}`. 409 when another reset is running or
the draft changed; 422 when the code is wrong, the draft no longer passes,
or the game isn't where config.toml says.

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 202

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/leagues/{league_id}/resets`

**List the League's Resets**

Every reset of the league, newest first: those performed from the console,
including failed ones, and (BRE) those only seen in a Game Start packet.

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

### GET `/management/api/v1/leagues/{league_id}/resets/{reset_id}`

**Get One Reset**

One reset with its progress, the driver's log, and which members have
downloaded the packets that carry it.

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**reset_id**
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

## Management API - Leagues

### GET `/management/api/v1/leagues`

**List All Leagues**

List all leagues with member counts

**Returns:** List of all leagues

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/leagues" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### POST `/management/api/v1/leagues`

**Create League**

Create a new league (admin only)

**Request Body:**
- `league_id`: 3-digit league number (e.g., "555")
- `game_type`: "B" for BRE, "F" for Falcon's Eye or "I" for Immortal Barons
- `name`: Display name for the league
- `description`: Optional description
- `dosemu_path`: Path to DOSemu installation (optional)
- `game_executable`: Game executable name (optional)
- `is_active`: Whether league is active (default: true)

**Returns:** Created league

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/leagues" \
  -H "Content-Type: application/json" \
  -d '{"league_id": "555", "game_type": "B", "name": "BRE League 555"}' \
  -b cookies.txt
```

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/leagues/{league_id}`

**Get League Details**

Get detailed information about a specific league

**Path Parameters:**
- `league_id`: Database ID of the league

**Returns:** League details with members and stats

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/leagues/1" \
  -b cookies.txt
```

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

### PUT `/management/api/v1/leagues/{league_id}`

**Update League**

Update a league (admin only)

**Path Parameters:**
- `league_id`: Database ID of the league

**Request Body:**
- `name`: New display name (optional)
- `description`: New description (optional)
- `dosemu_path`: New DOSemu path (optional)
- `game_executable`: New game executable (optional)
- `hub_fidonet_address`: The hub's own FidoNet address in this league
  (optional). Nodelist generation is blocked until this is set.
- `hub_routes_mail`: whether the hub's nodelist entry carries the game's
  HOST routing directive (optional).
- `is_active`: New active status (optional)

**Returns:** Updated league

**Example:**
```bash
curl -X PUT "https://hub.example.com/management/api/v1/leagues/1" \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Name", "is_active": false}' \
  -b cookies.txt
```

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/leagues/{league_id}`

**Delete League**

Delete a league and ALL associated data (admin only).

Permanently removes: league record, member associations, packets, processing
runs, sequence alerts, and all related files on disk (packet files and
nodelist directory).

**Path Parameters:**
- `league_id`: Database ID of the league

**Request Body:**
- `confirmation_name`: Must match the league's confirmation name, e.g. `BRE_014`

**Returns:** Success message

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### POST `/management/api/v1/leagues/{league_id}/members`

**Add Member**

Add a client to a league (admin only)

**Path Parameters:**
- `league_id`: Database ID of the league

**Request Body:**
- `client_id`: Database ID of the client to add
- `bbs_index`: BBS index (1-255)
- `ftn_address_id`: One of the client's own FTN addresses (assigned on the client)

**Returns:** Created membership

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/leagues/1/members" \
  -H "Content-Type: application/json" \
  -d '{"client_id": 1, "bbs_index": 2, "ftn_address_id": 7}' \
  -b cookies.txt
```

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/leagues/{league_id}/nodelist`

**Download Nodelist**

Download the league's current BRNODES/FENODES nodelist.

The same file nova_client fetches through the service API: the one the last
processing run (or Generate Nodelist) wrote. Fetching it here does not mark
it downloaded for any BBS.

**Path Parameters:**
- `league_id`: Database ID of the league

**Returns:** the nodelist file; 404 if none has been generated yet

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

### POST `/management/api/v1/leagues/{league_id}/generate-nodelist`

**Generate Nodelist**

Generate the league's nodelist from the current membership (admin only):
BRNODES/FENODES for the SR games, IBNODES (ibnodes.dat) for Immortal Barons.

The file is written to the nodelists directory, where the console and the
service API's bootstrap download serve it, and into the hub's own game as
its nodes file (only if that changes it). The game sends it to every member
on its next run, as it does when the file is edited by hand.

**Path Parameters:**
- `league_id`: Database ID of the league

**Returns:** `{"filename": ..., "members": <count>, "warnings": [...]}`; a
warning means the hub's game was not given the nodelist (no `game_folder`,
say), so the members will not receive it.

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

### DELETE `/management/api/v1/leagues/{league_id}/members/{member_id}`

**Remove Member**

Remove a client from a league (admin only)

**Path Parameters:**
- `league_id`: Database ID of the league
- `member_id`: Client ID to remove

**Returns:** Success message

**Example:**
```bash
curl -X DELETE "https://hub.example.com/management/api/v1/leagues/1/members/2" \
  -b cookies.txt
```

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**member_id**
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

### PATCH `/management/api/v1/leagues/{league_id}/members/{membership_id}`

**Edit Member**

Edit a league membership's BBS index and/or FTN address (admin only)

Both fields are validated before either is written, so a rejected edit
leaves the membership exactly as it was. The league's nodelist picks the
change up at its next regeneration.

**Path Parameters:**
- `league_id`: Database ID of the league
- `membership_id`: Database ID of the membership

**Request Body** (at least one):
- `bbs_index`: New BBS index (1-255)
- `ftn_address_id`: One of the client's own FTN addresses
- `board_key`: Immortal Barons leagues only. The board's public signing key,
  the 64 hex characters its `-gen-board-key` printed; `""` removes it

**Returns:** The updated membership

**Example:**
```bash
curl -X PATCH "https://hub.example.com/management/api/v1/leagues/1/members/1" \
  -H "Content-Type: application/json" \
  -d '{"bbs_index": 5, "ftn_address_id": 7}' \
  -b cookies.txt
```

#### Parameters

**league_id**
  - Type: `integer`
  - Location: path *(required)*

**membership_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Movements

### GET `/management/api/v1/movements/`

**List Item Movements**

Individual item movements, most recent first.

**Query Parameters:**
- `days`: how far back to look (default 7)
- `league_id`, `direction` (`in`/`out`), `item_type`, `node`: filters
- `node` matches either end of the transfer
- `run_id`: just this run's items, whatever their age

#### Parameters

**days**
  - Type: `integer`
  - Location: query

**league_id**
  - Type: `string`
  - Location: query

**direction**
  - Type: `string`
  - Location: query

**item_type**
  - Type: `string`
  - Location: query

**node**
  - Type: `string`
  - Location: query

**run_id**
- One run's items; ignores `days`
  - Type: `string`
  - Location: query

**limit**
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/movements/summary`

**Summarise Item Movements**

The shape of the traffic: by type, by node pair, and by day.

`quiet_nodes` lists boards this league has exchanged nothing with in the
window, which is the one thing worth pointing at -- not because it is
necessarily wrong (a node can be legitimately idle) but because it is the
question an admin would otherwise have to work out by reading the table.

#### Parameters

**days**
  - Type: `integer`
  - Location: query

**league_id**
  - Type: `string`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Processing

### GET `/management/api/v1/processing/runs`

**List Processing Runs**

List processing runs

**Query Parameters:**
- `limit`: Number of runs to return (default: 50, max: 200)

**Returns:** List of processing runs ordered by most recent first

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/processing/runs?limit=20" \
  -b cookies.txt
```

#### Parameters

**limit**
- Number of runs to return
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/processing/runs/{run_id}`

**Get Processing Run Details**

Get detailed information about a specific processing run

**Path Parameters:**
- `run_id`: Database ID of the processing run

**Returns:** Run details including packets processed, generated files, and logs

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/processing/runs/1" \
  -b cookies.txt
```

#### Parameters

**run_id**
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

### POST `/management/api/v1/processing/trigger`

**Trigger Processing**

Manually trigger packet processing (admin only)

**Returns:** Success/error status

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/processing/trigger" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

## Management API - System

### GET `/management/api/v1/system/version`

**Build Information**

What build is running.

The admin UI shows this in the sidebar, because otherwise the only place a
version appears is the login screen and you have to log out to read it.

**Returns:**
- `version`: release version string
- `revision`: short git commit, or null if the tree has no git metadata
- `released_at`: date of that commit (YYYY-MM-DD), or null
- `dirty`: true when the deployed tree has uncommitted changes

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

## Management API - Traffic

### GET `/management/api/v1/traffic/`

**List InterBBS Traffic**

Journeys with anything reaching the hub within `days`, newest first.

A journey is a send and its result (an op, a trade deal), a Gooie from its
funding News to its kill, or a single event (a message, a Spy Guy).

#### Parameters

**days**
  - Type: `integer`
  - Location: query

**league_id**
  - Type: `string`
  - Location: query

**planet**
- Either end
  - Type: `string`
  - Location: query

**limit**
  - Type: `integer`
  - Location: query

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/traffic/{key}/details`

**Reveal an Event's Contents (admin)**

Hidden game state: admin only, one event per request, logged.

A Message's content is refused to everyone.

#### Parameters

**key**
  - Type: `string`
  - Location: path *(required)*
  - Pattern: `^[0-9a-f]{16}$`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

## Management API - Users

### GET `/management/api/v1/users`

**List All Users**

List all sysop users (admin only)

**Returns:** List of all users

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/users" \
  -b cookies.txt
```

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

---

### POST `/management/api/v1/users`

**Create User**

Create a new sysop user (admin only)

**Request Body:**
- `username`: Username (must be unique)
- `password`: Password
- `is_admin`: Whether user should have admin privileges (default: false)

**Returns:** Created user

**Example:**
```bash
curl -X POST "https://hub.example.com/management/api/v1/users" \
  -H "Content-Type: application/json" \
  -d '{"username": "newuser", "password": "secret", "is_admin": false}' \
  -b cookies.txt
```

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### GET `/management/api/v1/users/{user_id}`

**Get User Details**

Get details about a specific user (admin only)

**Path Parameters:**
- `user_id`: Database ID of the user

**Returns:** User details

**Example:**
```bash
curl -X GET "https://hub.example.com/management/api/v1/users/1" \
  -b cookies.txt
```

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

### PUT `/management/api/v1/users/{user_id}`

**Update User**

Update a sysop user (admin only)

**Path Parameters:**
- `user_id`: Database ID of the user

**Request Body:**
- `username`: New username (optional)
- `password`: New password (optional)
- `is_admin`: New admin status (optional)
- `is_active`: False disables the account: it can't sign in, and its
  session stops working (optional)

**Returns:** Updated user

**Example:**
```bash
curl -X PUT "https://hub.example.com/management/api/v1/users/1" \
  -H "Content-Type: application/json" \
  -d '{"username": "updateduser", "is_admin": true}' \
  -b cookies.txt
```

#### Parameters

**user_id**
  - Type: `integer`
  - Location: path *(required)*

### Request Body

**Content-Type:** `application/json`

#### Responses

#### 200

Successful Response


**Content-Type:** `application/json`

#### 422

Validation Error


**Content-Type:** `application/json`

---

### DELETE `/management/api/v1/users/{user_id}`

**Delete User**

Delete a sysop user (admin only)

**Path Parameters:**
- `user_id`: Database ID of the user

**Returns:** Success message

**Note:** Cannot delete yourself

**Example:**
```bash
curl -X DELETE "https://hub.example.com/management/api/v1/users/1" \
  -b cookies.txt
```

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

### POST `/management/api/v1/users/{user_id}/relink-link`

**Issue Re-link Link**

Issue a single-use link that connects a sign-in to this account (admin only).

Whoever opens it and signs in through the identity provider has that sign-in
bound to this account, replacing any it had. Use it for an account that
predates provider sign-in, or a sysop who has lost access to their email.
Expires after 72 hours; supersedes any outstanding link for the account.

**Returns:** the link (shown only here) and its expiry

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

### GET `/management/api/v1/users/{user_id}/relink-link`

**Re-link Link Status**

The latest re-link link issued for this account, or null (admin only). Never the link itself.

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

### POST `/management/api/v1/users/{user_id}/unlink`

**Remove Sign-in**

Disconnect this account's provider sign-in (admin only).

It can no longer sign in through the provider until a re-link link is used.
Sessions already open last until they expire. Refused for an account that
would then have no way in at all, unless it is not your own.

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
