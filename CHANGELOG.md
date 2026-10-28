# Changelog

All notable changes to Nova Hub will be documented in this file.

## [Unreleased]

### Security
- **A member can no longer speak for the coordinator.** League settings and
  the reset history came from any uploaded packet's Configupdate or Game
  Start, so a member could rewrite what the hub shows of a league. Those two
  records are now believed only from the hub's own packets (source = the
  hub's index), and any other record only when its sender is the board that
  uploaded it. The hub relays other boards' records, so its own packets are
  exempt. Refused records are logged and skipped; the packet still routes. (#88)
- **The BRE parser has size limits.** A record may decompress to 16 KB and a
  packet to 1 MB; past that the packet is undecodable, as a bad one already
  was. The largest real record is 2.7 KB. Before, a 60 KB upload could expand
  to gigabytes in memory, even with a bad CRC. (#88)
- Damaged numbers and dates in a record (a short Real, a date past year
  9999, a loss that isn't a fraction) read as unknown or undecodable rather
  than raising. A reveal of a damaged record gives 422, not 500, and reveals
  read only records whose CRC checks. (#88)
- **A service token is no longer a session.** Both are signed with the same
  key, and the claim page's optional sign-in didn't check which kind it had,
  so a BBS whose client ID matched a sysop's username could act as that sysop
  there. (#87)
- **X-Forwarded-For/-Host/-Proto are believed only from the reverse proxy**
  (`[security] trusted_proxies`, default the loopback addresses), and the
  address used is the one the proxy appended. Before, any client could pick
  the address the rate limiter, the audit log and claim links saw. Behind
  Caddy as deployed this wasn't reachable; on a hub serving directly it was.
  The limiter also forgets addresses that went quiet. (#87)
- **Sign-in redirects stay on the hub** even when the path hides a tab or
  newline, which browsers drop to make `//elsewhere`. (#87)
- A transcript or score file that can't be converted to HTML is shown
  escaped, not as raw HTML; the converter's failures and hangs (20s) now count
  as failures. (#87)
- A join application is refused if a field holds a newline or control
  character: approval copies them into nodes.dat. Config files the hub writes
  escape control characters, and a BBS name can't close the PowerShell
  file's comment or its quotes (including typographic ones). (#87)
- An unknown BBS or username takes as long to refuse as a wrong secret, and
  input bcrypt refuses counts as a failed attempt rather than an error. (#87)

### Added
- **Immortal Barons leagues** (game letter `I`), from andy5995's PR. IB is a
  native Go binary, so it gets its own game provider rather than the BRE/FE
  code paths (#100):
  - The hub is the league's Coordinator, node 1, and runs IB natively
    (`[ib.<league>]` in config.toml; logs in `logs/native/`). A run that
    exits non-zero still delivers what IB wrote and is marked failed.
  - A broadcast (destination 00) is a row per receiving board. A re-upload of
    the same bytes is the same packet; a different file under a used name is
    a 409. The hub never renames or overwrites an IB packet, since IB reads
    only `*.brp` and would miss an FTN-style `.br1`.
  - Generate Nodelist writes the roster, with each board's signing key as the
    optional seventh line, to the hub's IB and publishes it to members as
    `IBNODES.<league>`. A member's key is set on the league page.
  - No sequence-gap checks, and no reset editor (404). IB packet names never
    repeat, so their files expire after `retention_days`, like logs.
- **Disable an account** without deleting it (Admin > Users > Edit). A
  disabled account can't sign in by either route, and its session stops
  working. Password sign-in now records the last sign-in, as the provider
  route did. (#87)
- **The token endpoint speaks more OAuth2.** The response carries
  `expires_in` (seconds) and `Cache-Control: no-store`, and a client may send
  its id and secret as HTTP Basic instead of in the form (RFC 6749 2.3.1),
  as stock OAuth2 libraries do. Sending both, or neither, is a 400; it was a
  422 for neither. (#89)
- The Service API spec says what its binary endpoints carry: an
  `application/octet-stream` body on upload, bytes from a packet or
  nodelist download, and the nodelist's 304 and conditional headers. (#89)
- League ids in Service API paths may use a lowercase game letter (`555b`). (#89)

### Changed
- The per-game work (processing, outbound collection, nodelists, client
  config) moved from `ProcessingService` into game providers,
  `backend/services/providers/`. BRE and FE behave as before. (#100)
- **The database enforces its references.** SQLite ignores foreign keys
  unless asked on every connection, so every reference and `ON DELETE` in the
  schema was decoration. The hub's engine now turns them on, runs in WAL mode
  (client polls no longer wait on a processing run's writes) and waits up to
  30 s for a lock rather than failing. (#91)
- `deploy/nova-hub.service.j2` adds `NoNewPrivileges`, `PrivateTmp`,
  `ProtectSystem=full` and `UMask=0077`, and `deploy.sh` makes its database
  backup readable by the hub's user only. The unit file is installed by the
  playbook, not `deploy.sh`, so a running hub keeps its unit until that is
  re-run or the file is copied by hand. (#92)

### Fixed
- **Every stored time is UTC.** Packet download, processing, alert resolution
  and the dashboard's stats used the host's local clock while everything else
  used UTC. On a host outside UTC that shifted run durations, the MIT
  window's checks and the reset confirmation window. Every hub host so far
  runs in UTC, so no stored data is affected. (#89)
- `received_at` and `retrieved_at` in the Service API's packet list end in
  `Z`, as the spec's `date-time` promises. (#89)
- A run's duration is never negative and counts past a day. (#89)
- **Deleting a league left rows behind**: its attack and traffic sightings,
  game settings, resets and run items, all pointing at a league that was
  gone. With foreign keys on, the delete would have failed outright. It now
  clears every table that names the league, and a run that also carried
  another league's packets leaves those packets in place. (#91)
- League creation and deletion are in the audit log. (#91)
- A new league's number must be three digits; anything else made a league
  no packet could be addressed to. (#91)
- `reset_database.py` stamps the new database at the current migration, so
  the next `alembic upgrade head` doesn't replay them all. (#91)
- **The dashboard's 24-hour cards always said 0.** Packets and processing
  runs in the last day are now counted (for a sysop, their own). (#92)
- **A league's "processed" and "runs" counts always said 0.** They counted a
  flag only nodelists ever set and a run column no run fills in. They now
  count packets with a processed time, and the runs that moved or made
  something for the league. Processing sets the flag from now on. (#92)
- The activity chart is one query, not one an hour (168 for the week), and
  the activity feed no longer looks up clients a packet at a time. It also
  reads the hub's index from the config rather than assuming `01`. (#92)
- `run.py --config` passes the file on to the app it starts; processing
  re-reads that file each batch, not whatever `config.toml` is in the
  working directory. (#92)
- `purge_retention.py` reaches the dosemu transcripts on disk, as the
  hub's own daily pass does, and reports them. (#92)
- `/health` gives 503 when the database fails, and no longer returns the
  error text to an unauthenticated caller. (#92)
- Signing out reloads the page, so nothing the last session loaded is still
  in memory for the next person to sign in. Form errors that FastAPI reports
  as a list read as sentences in every view. (#92)
- **A packet uploaded while processing ran waited for the next upload.**
  The hub has no poll loop, and a trigger during a run was dropped, so the
  last packet of a sync could sit in inbound for a day. The run now goes
  round again when it finishes. (#90)
- **A dosemu run that timed out kept running.** The timeout stopped waiting
  but killed nothing, so a hung game held its folder (and `inuse.flg`)
  against the next run. A timeout now kills script, dosemu and everything
  they started, including a process that ignores the hangup or has been
  orphaned. (#90)
- **An upload retried after a dropped connection made a second packet.**
  The same name and bytes within a day now answer with the packet the hub
  already has. (#90)
- Uploads no longer create a league for any number a client names: an
  unknown league is a 404, and a disabled one, which is never processed, a
  403. A bad `Content-Length` is a 400, not a 500, and a body is refused as
  soon as it passes the size limit rather than after it is all read. (#90)
- An uploaded packet records which client sent it (`source_client_id`). (#90)
- A member not yet given a board index gets a 403, or an empty list, not a
  500. (#90)
- With sequence alerts enabled, a gap on a route that had wrapped was
  resolved as "received" the moment it was raised, by the same number from
  the time before. Resolution now matches the time round too. (#90)
- Resolving or reopening an alert by hand now updates what the validator
  reads, so a hand-resolved alert is no longer resolved again over the top. (#90)
- A file in a game's outbound folder that isn't a packet is passed over
  with a warning rather than logged as an error. (#90)
- A reset's restore swaps the folders by rename, so there is never a moment
  with no game folder, and it refuses links out of the folder. (#90)

### Removed
- The outbound folder watcher. It ran at every start, but in three months
  of production it registered nothing: the hub's game writes packets only
  during processing runs, which collect them themselves. `watchdog` is no
  longer a dependency. (#90)

### Upgrading
- One migration (`f1c6b2a8d407`) adds `league_memberships.board_key`;
  nothing is rebuilt. (#100)
- One migration (`e7a2c4d9b153`), indexes only, so no table is rebuilt: the
  client poll's index, the run lookups, and unique indexes for one league
  per number and game, one membership per BBS per league, and one reset
  under way at a time. It also clears two packets' references to processing
  runs that no longer exist. Rehearsed on a copy of production: under a
  second, both ways, and `foreign_key_check` is clean afterwards. (#91)
- WAL keeps `nova-hub.db-wal` and `nova-hub.db-shm` beside the database.
  Back up with `sqlite3 nova-hub.db ".backup <copy>"`, as `deploy.sh`
  does; a plain copy of `nova-hub.db` alone can miss recent writes. (#91)

## [0.9.0] - 2026-10-19

### Added
- **Sysops rotate their own BBS's secret.** Regenerate Secret on the client
  page is no longer admin-only: a BBS's owners have it too. It offers to keep
  the old secret working for 24 hours (the API takes up to 72), so the node
  isn't cut off before its config is edited, or to stop it now. Stopping it
  now also ends the access tokens already issued, which otherwise live 24
  hours: the usual reason to rotate in a hurry is a secret that got out. The
  client page says until when the old secret works, and the audit log
  records which was chosen. A claim link's new secret stops the old one the
  same way.

### Upgrading
- One migration (`d4f1b7c2e980`): three nullable columns on `clients`.
- Access tokens issued before this release have no issue time, so the first
  rotation that stops a BBS's old secret ends them too, as intended.
- Correct the stored Movements: `.venv/bin/python reparse_movements.py` shows
  what it would change; run it again with `--apply`.

### Fixed
- **Movements named boards that don't exist** (1->31194, 1->683 on prod).
  The game paints its transcript as a screen: it wrote a destination, then
  jumped the cursor to repaint another record, and dropping that jump joined
  the two numbers. It could as easily have made a believable wrong node. A
  cursor move now ends the line, and cursor-forward is a space, which also
  recovers records the parser used to miss. `reparse_movements.py` rewrites
  the rows of every run whose transcript is still held (dry run unless
  `--apply`); in older runs a node over 255 becomes unknown, shown as `?`.
- **Movements showed a league's name without its game**, so a BRE and an FE
  league of the same name looked alike in the table and the filter. It now
  reads "015B League 015", as Attacks and Traffic do.

### Changed
- The Game reset page no longer explains the editor's league-wide question.
  The hub always answers yes, and anyone on the page is there for exactly
  that, so the game's help for it said nothing useful. The form's
  `league_wide_help` field is gone from the Management API.
- The test suite runs with no warnings (it printed 2,067): `datetime.utcnow()`
  is replaced by `backend.core.clock.utcnow()`, which gives the same naive UTC,
  and Pydantic models use `ConfigDict`.

## [0.8.0] - 2026-10-17

### Added
- **Reset a game from the console.** A league's Game reset page lists every
  setting of the game's own reset editor (BRE and FE), in its order, with the
  game's help, default and limits and our notes where they disagree. An admin
  saves a draft, then PERFORM RESET types it into `<EXE> RESET` through
  sr-harness rather than writing `GAME.DAT`, which is checksummed. The hub
  takes the processing lock, backs up the game folder (the last 5 are kept),
  reads both editor pages back against the draft before saving, and on any
  failure restores the folder byte for byte. A PLANETARY run then sends the
  Game Start. Advised minimums warn rather than refuse; a typed league code
  confirms. Rehearsed on a copy of production's 015B and 015F.
- **Reset history**, on the reset page and the league page (admins only).
  Every Game Start the hub passes is recorded: it confirms the console reset
  before it field by field, or lists what disagrees, or is kept as a reset
  seen in packets. `backfill_reset_history.py` reads the packets already on
  disk. A row opens in place into its steps, editor log and what it changed.
- **BRE Configupdate decoded.** Every reset setting but the start date, so
  the form's "current" column shows the league's live settings. It fills in
  at each league's first Configupdate after upgrading.
- **A note on dates and timezones** on the reset form: the hub's zone, and
  that each BBS applies the dates on its own clock.

### Fixed
- **Processing runs named no league.** A run takes every league with work,
  so its `league_id` was never set and the League column always read "-".
  Runs now report `leagues` (e.g. `["015B", "015F"]`) from the packets they
  moved and the files they made; `league_name` is the same list joined.
- **A run's scores looked incomplete.** The Score Files tab listed cards by
  filename alone, so a two-league run showed two `BBSSCORE.ANS` with nothing
  to tell them apart. Files are now grouped by league.
- **Packets counted as waiting after their download.** 1,991 rows from
  11-12 January 2026 have a download time but `is_downloaded` false; a
  download time now counts, and migration `e7b3d1a9c524` sets the flag.
- **Sticky headers and toolbars never stuck**: the main content area was its
  own scroll container though the window is what scrolls.

### Upgrading
- Four migrations (`alembic upgrade head`): `b6d2e8f4a170`, `d4a9c2e7b815`,
  `e7b3d1a9c524`, `a3c8e1f6d092`.
- New runtime dependency: `sr-harness` v0.1.1 (with `pyte`, `wcwidth`),
  installed from gitea. Install `requirements.txt` before restarting.
- Startup now settles any reset a restart interrupted; with none, it does
  nothing.

## [0.7.1] - 2026-10-14

### Changed
- **The lock no longer downgrades production.** 0.7.0's lock was seeded from
  the dev venv, which was behind production on seven packages. They are now
  locked at production's versions: certifi 2026.7.22, cryptography 46.0.5,
  multidict 6.7.1, pyasn1 0.6.2, pycparser 3.0, soupsieve 2.8.3, websockets
  16.0. 0.7.0 was never deployed.

## [0.7.0] - 2026-10-14

### Fixed
- **A board added through self-service never reached the other boards.**
  Approving it regenerated the hub-side nodelist (the one you can download),
  but members only receive a nodelist when the hub's own game sends it, which
  BRE does on a maintenance day after its `BRNODES.DAT` changes. Nothing
  changed that file, so the new board was missing from the hub's game and
  from every member (prod, 015B/015F, Tentacle BBS). Generating a nodelist
  now also writes it into the hub game's nodes file, under the name and case
  the install already uses, and only when the content differs, so an
  unchanged nodelist is not resent. This covers approvals, membership edits
  (at the next processing run) and `POST /leagues/{id}/generate-nodelist`,
  which now returns `warnings` when a league has no usable `game_folder`.
  Proved on the live rig: `tests/live/test_nodelist_publish.py`.

### Added
- **Sample client config on the BBS's page.** The Client Information card
  links a `config.toml` (Linux) and `config.psd1` (Windows) for the BBS, for
  its own sysops and admins (`GET /clients/{id}/config/{linux|windows}`). It
  is the file a claim hands back, with the hub address, client ID and
  current leagues filled in, but the secret is a CHANGE-ME like the game
  paths: the hub keeps only its hash. Downloading changes nothing.

### Changed
- **Dependencies are locked with uv.** `pyproject.toml` lists them as ranges,
  `uv.lock` holds the exact versions (indirect dependencies included), and
  `uv sync` builds `.venv` from it. `requirements.txt` is now exported from
  the lock, so the deploy playbook's `uv pip install -r requirements.txt`
  installs exactly what CI tested; CI fails if it falls out of step. pytest
  and pytest-asyncio moved to the dev group and are no longer installed in
  production. No version changed.
- **FastAPI 0.128.0, Pydantic 2.12.5, pydantic-settings 2.12.0** (were
  0.115.0, 2.10.0, 2.6.0). Deploying needs `uv pip install -r
  requirements.txt` before the restart.
- **API docs are split per API and kept current.** `docs/API.md` and
  `docs/openapi.json` mixed both APIs in one file with one shared schema
  list, and had not been regenerated since 0.5.0. `export_api_docs.py` now
  writes `docs/service-api.{json,md}` and `docs/management-api.{json,md}`,
  each spec exactly what that API serves at its `openapi.json`, keys sorted
  (the service spec is the one `export_openapi.py` gives nova-client).
  `tests/test_api_docs.py` fails while they are stale, so regenerate them in
  the commit that changes an API, a release's version bump included.
- **The live rig drives the games through sr-harness.** The rig's pty runner,
  screen driver, 8.3 guard and BRE player walk were split out as the
  standalone `lime/sr-harness` library (which also plays Falcon's Eye), and
  the rig now installs it, pinned to v0.1.1, from `tests/live/requirements.txt`.
  CI installs it too. Test-only: nothing in the product changed.

## [0.6.0] - 2026-10-12

### Added
- **Sysops apply to join; the admin approves.** A public `/join` page takes a
  signed-in sysop's BBS details (name, sysop alias, location, address, OS) and
  the leagues they want. The admin reviews the queue under Admin >
  Applications, picking each league's BBS number and FTN address (suggested:
  the BBS's number elsewhere, else the lowest free one). Approving creates the
  BBS, makes the applicant its owner, adds the memberships, regenerates the
  leagues' nodelists and issues a claim link bound to the applicant, all in
  one transaction. Rejecting records a note the sysop sees. A sysop can
  withdraw while it waits; one waiting application per account.
- **More leagues from the console.** An owner asks for more leagues on their
  BBS's page; approval adds the memberships, and the credentials don't change.
- **Claim links bound to an account.** Only that account, signed in, can
  claim one, so a forwarded link is useless to anyone else. Approval issues
  them; the admin can also bind a link to one of a BBS's owners. The bound
  sysop collects it from their BBS's page. Unbound links still work for a
  sysop with no account.
- **Verify progress** on each BBS's page: Approved, Credentials claimed,
  First sign-in, First upload, Active (seen in the last two days), so a node
  stuck at a step shows without asking. The hub now records each BBS's first
  sign-in and first upload; the upgrade fills both in for BBSes already
  running.

### Changed
- Attacks & Traffic labels each league with its number and game ("015B
  Medusa Test League 015"), since a BRE and an FE league can share a name,
  and has a Refresh button that keeps the filters.
- The console's sidebar stays the height of the window, so sign-out and the
  version stay on screen on a long page.

### Fixed
- Opening /join or /how-it-works directly while signed out went to the
  sign-in page instead.

### Upgrading
- One migration: the applications table, each BBS's first sign-in and first
  upload (filled in from its history), and claim links bound to an account.
  Claim links already issued stay unbound and keep working.

## [0.5.1] - 2026-10-12

### Added
- **A public "How it works" page** at `/how-it-works`, for a sysop deciding
  whether to join. It maps each FTN piece (mailer, session password, tosser,
  nodelist, maintenance event) to what replaces it, says what stays on their
  machine, which client to run on Windows or Linux, what a good first run
  looks like, and the gotchas that have bitten live nodes. No sign-in needed;
  linked from the sign-in page and from claim links. The client download is
  the page's call to action; the hub's own source sits apart in a "For the
  curious" box.
- `export_openapi.py` writes the Service API's OpenAPI spec as JSON, with the
  hub version in `info.version`. nova-client vendors it as
  `openapi/service-api.json` and checks its mock hub against it in CI.
- Nodelist downloads honour conditional requests (#80).
  `GET /service/api/v1/leagues/{id}/nodelist` answers `304 Not Modified` to a
  matching `If-None-Match`, or to an `If-Modified-Since` no older than the
  file. The hub already sent `ETag` and `Last-Modified` but never answered 304,
  so the PowerShell client re-fetched an unchanged nodelist on every sync. A
  304 leaves `downloaded_at` alone, so it still means the last real transfer.
- An admin can rename a league and edit its description from the league's
  Edit dialog. The name is only shown in the console; the league number and
  game, which the games and nodelists use, don't change. Each edit to a
  league is recorded in the audit log.

### Fixed
- Deleting a BBS that was still in a league failed with a server error. The
  hub now refuses, naming the leagues, and the Delete dialog says so before
  you press it: remove the BBS from its leagues first.
- **"Routes/BBS info command failed: Unknown error" after every run (#83, #85).**
  BRE and FE end `ROUTEINFO` and `BBSINFO` with DOS errorlevel 1 even when they
  succeed. Since 0.3.0 passed the real exit code through (`script -e`), each
  working run logged two false warnings (1,204 pairs on production since
  2026-09-10), while `routes.lst` and `bbsinfo.lst` were written and ingested
  every time. These two commands are now judged by whether their listing file
  was written during the run, and a warning names the stale file when it was not.
- **No more "Unknown error".** A non-zero dosemu exit now reports its exit code
  and transcript path, for every command including `PLANETARY`.
- **Hub logs are plain text in the journal (#84).** loguru was forced to colour
  its output, and journald stores a line containing ANSI escapes as an array of
  byte values, so hub lines reached OpenObserve as `["27","91",...]` and could
  not be searched. Colour is now used only when stdout is a terminal.

### Upgrading
- `backfill_attacks.py` fills the Attacks and Traffic views from the packets
  still on disk, for an upgrade from 0.4.0 or earlier. It only adds rows and
  can be run again safely.

## [0.5.0] - 2026-10-12

### Added
- **Claim links** replace sending a client secret over chat. An admin issues
  one from the BBS's page; the sysop opens it and presses Claim, and the hub
  generates the secret then and shows it once, with a ready-made client config
  (`config.psd1` for Windows, `config.toml` for Linux) listing their leagues
  and BBS numbers. A link works once, expires after 72 hours, and a newer one
  supersedes it; a used link says when and from what IP. Only the token's hash
  is stored. Opening the link changes nothing -- claiming is a button -- so a
  chat app's link preview cannot use it up.
- `[server] public_url`: the address sysops reach the hub at, used in claim
  links and the configs they hand out.
- `GET /service/api/v1/me`: the calling BBS and the active leagues it is in,
  with its BBS index in each. Nova Client's connection test
  (`--test-connection` / `-TestConnection`) compares these with its config,
  and the claim page now tells sysops to run it first.
- **Address book** (`/address-book`): every BBS against every active league,
  with its index and FTN address in each. A BBS whose index differs between
  leagues is flagged.
- Adding a BBS to a league pre-fills its index from its other leagues when
  they agree, and says so when they do not.
- **FTN addresses belong to the BBS.** An address is held by one BBS, unique
  across the hub, and a BBS may hold several; each league membership links to
  one of its own BBS's addresses. Before this an address was free text on each
  membership, and production had `135:135/20` assigned to two BBSes in two
  leagues. Admins assign, renumber and remove addresses on the client page; an
  address still used by a league cannot be removed.
- **Edit a league membership** in place -- its BBS index and address -- rather
  than deleting and re-adding it. `PATCH /leagues/{id}/members/{membership_id}`.
- **Download a league's current nodelist** from the league page, and from each
  membership on the client page (`GET /leagues/{id}/nodelist`).
- `tools/release.sh <tag>` publishes a version's section of this file as the
  release notes on GitHub and gitea.
- MIT licence.
- **Sysop accounts.** Sysops sign in through an identity provider (WorkOS
  AuthKit), configured in a new `[identity]` table; with it unset the hub
  works as before. The first sign-in makes a sysop account that owns nothing
  and sees nothing until an admin adds it as a BBS's sysop (the new Sysops
  card on the client page). The hub admin's password still works, as the way
  in when the provider is down. A sign-in is never attached to an existing
  account by matching email: an admin issues a **sign-in link** from the Users
  page, which works once, for 72 hours, for the account it names. Admins can
  also remove an account's sign-in.
- **What a sysop sees** is limited, on the server, to their own BBSes and the
  leagues those are active in: dashboard, alerts, processing runs (their own
  packets, no transcript), leagues (active members, no install paths), the
  address book and nodelists. Anything else is a 404. A test fails if a
  management route is added without either admin-only or this scope.
- **Audit log** (`/admin/audit`, `GET /audit`): who created, changed or
  deleted a BBS, issued or used a claim or sign-in link, assigned an FTN
  address, added an owner, or changed a user or role, with when and from what
  IP.

### Changed
- Each game's names (letter, code, nodelist and nodes-file names) live in one
  table, `backend/services/games.py`, mirrored in `frontend/src/games.ts`. An
  unknown game letter is now an error instead of being treated as Falcon's Eye.
  Contributed by Andy Alt (GitHub PR #1).

- Movements, attacks and the traffic view are admin-only: they show every
  league's game intelligence.
- An admin cannot remove their own admin role.

### Removed
- `PUT /leagues/{id}/members/{membership_id}/bbs-index` and `.../fidonet`,
  replaced by the `PATCH` above.

### Upgrading
- Migration `a7d3e9f15b20` copies each membership's address onto its BBS. It
  refuses, naming them, if one address is held by more than one BBS; give one of
  them a different address and re-run. Nothing is changed until it succeeds.
- Migrations `6d2f8a41c9e7` and `9a4b2e7c1f05` add provider sign-in to user
  accounts, BBS owners, the audit log and sign-in links. Existing accounts
  keep their passwords.
- To turn on provider sign-in, fill in `[identity]` (see
  `config.toml.example`) and register
  `<public_url>/management/api/v1/auth/sso/callback` as a redirect URI with
  the provider. Then connect the admin account's own sign-in with a link from
  the Users page.
- Reinstall the requirements (`pip install -r requirements.txt`): `httpx` is
  now needed at runtime, for provider sign-in, and `aiohttp` moves to 3.14.3.

## [0.4.0] - 2026-09-17

Shows what the games actually exchanged, and stops guessing about what went
missing.

The question this release answers -- "a player says an attach went missing in
transit, what does the hub know?" -- turned out not to be answerable by counting
sequence numbers. Both games burn a number at every game-day rollover, so across
eight months of production data every busy route shows exactly one lone missing
number per active day (232 days of 239 on one route, 237 of 241 on another, 109
of 111 on a third), each at that route's own maintenance time. A lone gap is the
game working normally and cannot be told apart from a real loss. So the answer is
no longer an alarm; it is the games' own account of what moved, shown to someone
who knows what their league should look like.

### Added
- **Movements.** Every item the games exchange -- its type, and which node sent
  it to which -- is read from each run's `/DETAILED` output and recorded in
  `processing_run_items`. A new `/movements` page leads with the shape of the
  traffic (by type, by node pair, by day) and a filterable table underneath;
  each processing run gains a "What Moved" tab grouped by route. Stored rather
  than parsed on demand because retention drops transcripts after 30 days, and a
  history that silently empties after a month is worse than none.
- `tools/backfill_movements.py` recovers movements from transcripts still held
  on existing runs -- about 1,176 runs on production, the retention window.
  Report-only by default, skips runs already recorded, inserts only.
- `tests/live/serve.py` serves the rig's hub with a real uvicorn and the built
  frontend, so the UI can be looked at with real rig data. `--build` plays an
  actual round first. The scenarios drive the hub in-process, so until now
  nothing listened and the rig's UI could not be seen at all.

### Changed
- **Sequence gap alerting is off by default** (`[processing]
  sequence_alerts_enabled`). Enabled, it fires about three times a day on
  production and every one of them is the daily rollover described above; the
  real loss it exists to catch would arrive as the fourth identical line that
  day. That is how the previous detector reached 705 unresolved false alerts and
  made a genuine loss unfindable. Gaps are still detected and recorded either
  way -- what is switched off is ringing a bell about them. Set it true to
  restore the alerts.
- Sequence alerts, when enabled, are bounded to recent gaps
  (`sequence_alert_max_age_days`, default 14). A missing packet is actionable
  only while it might still be chased. Against production's history: no bound
  599 alerts, 30 days 90, 14 days 42, 7 days 21.

### Fixed
- The transcript parser invented item types. dosemu repaints the emulated
  screen, and a painted line runs two records together with no separator, so a
  loose type pattern ran past the end of the first record and reported types
  like `Recon Request DeCompress: Old: 2 New: 2 %: 0.0% Type: Recon Request`.
  Harmless in a command-line tool, a row in a table someone reads once it is a
  product feature. It also existed in two copies, which is how the bug survived
  in one after being understood in the other; both are now thin wrappers over
  `backend/services/transcript_service.py`.

### Fixed
- Sequence gap detection was blind on every route that had wrapped, which by now
  is every busy route in production. Wrap-around was *inferred* -- sort the
  numbers, find the one gap wider than 500, splice there -- which can represent
  exactly one wrap. League 3's route 02->01 has sent 5,891 packets over eight
  cycles, and sorted into a set those collapse into a flawless 000-999 run with
  no gap to find, so the detector reported nothing missing there no matter what
  went astray. Cycles are now counted from arrival order instead, and the gap
  arithmetic runs in a space that only increases, where a wrap is a step of one
  and needs no special case.
- A game reset restarts the numbering early, and used to look like either a wrap
  or a loss of everything between where the game stopped and 999. Resets are now
  told apart from wraps by where the old cycle ended, and the distance across one
  is not counted as missing packets. Production's busiest route has two, at 494
  and 633.

- Gap detection reported a confident "no gaps" on the hub's own outbound routes,
  which it cannot know. Packets the hub generates are recorded by
  `collect_outbound_packets`, which updates the existing row when a filename is
  reissued rather than adding one -- right for delivery, since it resets
  `is_downloaded` so the client re-fetches, but it means a wrapped route's rows
  are a fixed table of 1,000 slots instead of a history. Those routes are now
  skipped rather than given a false all-clear.

### Added
- `tests/live/rig/grind.sh` walks a rig install through the whole sequence space,
  and `tests/live/rig/dupseq.sh` checks whether a receiving game accepts a
  reissued filename. Both run unattended.
- `sequence_alerts.sequence_epoch` records which time round the numbering a gap
  was, so an alert is identified per cycle. Without it "missing 992" names one
  packet per cycle and a stale alert could swallow a real loss.
- `tools/sequence_timeline.py` reports a route's wraps and resets from exported
  data, without running the hub.

## [0.3.2] - 2026-09-15

### Fixed
- Retention honoured `retention_days` in the database and ignored it on disk.
  Every dosemu transcript is written to `<data_dir>/logs/dosemu/*.log` before it
  is copied into `ProcessingRun.dosemu_log`, so blanking the column left the
  file: production held 53,930 log files and 860 MB across eight months while
  the database tier was working correctly. The daily pass now deletes aged log
  files too, bounded to that one directory and to `.log` files in it, without
  recursing or following symlinks.

### Added
- `deploy.sh` prunes its own database backups, keeping the three most recent.
  Each deploy leaves a ~70 MB copy, and nothing had a ceiling.
- `deploy/deploy.sh` and `deploy/prune_backups.sh` are in the repository. The
  script that deploys production had existed only on production.

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


## [0.1.1] - 2026-07-24

### Fixed
- dosemu failing to start when the hub has no controlling terminal.

## [0.1.0] - 2026-01-08

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

## Known issues

### A genuine Falcon's Eye packet loss does not raise an alert
The sequence validator's density and stride gates stop `015F`'s naturally sparse
numbering from raising false alerts, but the cost is symmetrical: a real lost
Falcon's Eye packet no longer raises one either. Separating a true loss from
ordinary sparse numbering needs the game's own `ROUTEINFO` output, not arithmetic
over filenames.

### Wrap-around is inferred from the data, not tracked
Sequence numbers run 000-999. The validator finds the wrap by looking for a step
larger than 500 inside the window it was handed. That holds for a single wrap in
dense traffic, but it cannot tell a wrap from a genuinely large gap, and it has
nothing to say about a window spanning more than one wrap. Carrying an epoch
alongside the number would remove the guess.

### `passlib` is an unused dependency
`requirements.txt` pins `passlib[bcrypt]==1.7.4` and `bcrypt==4.0.1` against a
compatibility problem that no longer applies: nothing imports passlib. Both the
application (`backend/core/security.py`, `backend/models/database.py`) and
`create_admin_sql.py` call bcrypt directly.

## Upgrading

Production is pinned to a tag and deploys from gitea. On `novahub-vtr`:

```bash
sudo -niu novahub /home/novahub/deploy.sh          # what is running, tags available
sudo -niu novahub /home/novahub/deploy.sh v0.3.1   # pin to a version
sudo systemctl restart nova-hub                    # deliberately separate
```

`deploy.sh` backs up the database, checks the tag out on a detached HEAD,
rebuilds the frontend, applies migrations and validates the config. It refuses to
run if the working tree is dirty, since checking out over hand-edited production
files would destroy them silently.

Two things it deliberately does not do. It does not restart the service -- that
stays a human decision. And it does not touch `config.toml` or `frontend/dist`,
both of which are gitignored; `dist` is rebuilt rather than delivered, which is
why a bare `git checkout` would otherwise leave the UI on the previous version.

After a restart, hard-reload the browser: `index.html` is not content-hashed, so
a cached copy keeps asking for the old bundle.

## Contributing

When making changes:
1. Update relevant documentation
2. Add entry to this changelog
3. Create database migration if schema changes
4. Update requirements.txt if dependencies change
5. Test upgrade path from previous version
