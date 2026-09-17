# Changelog

All notable changes to Nova Hub will be documented in this file.

## [Unreleased]

Nothing yet.

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
