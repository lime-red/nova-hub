# DOSEMU Setup Guide for Nova Hub

Nova Hub runs BRE (Barren Realms Elite) and FE (Falcon's Eye) game processing
inside **dosemu2** — a Linux DOS emulator.  This document explains how the hub
maps its data directories into the DOS environment and how to configure it.

---

## Overview

When a batch of packets arrives from BBS nodes, Nova Hub:

1. Copies inbound packets to the game's inbound folder.
2. Launches dosemu2 with a per-game configuration file.
3. Runs the game's processing command inside dosemu2.
4. Collects outbound packets from the game's outbound folder.
5. Runs auxiliary commands (scores, routes, bbsinfo) and ingests their output.

Each **league × game type** combination has its own isolated directory tree and
dosemu2 configuration so multiple leagues can coexist safely.

---

## Directory Layout

```
<data_dir>/
└── dosemu/
    └── <league_number>/          # e.g., "555"
        └── <game_type>/          # "bre" or "fe"
            ├── inbound/          # Packets waiting for the game to process
            └── outbound/         # Packets the game generated for distribution

<data_dir>/
├── packets/
│   ├── inbound/    # Received from clients (before processing)
│   ├── outbound/   # Ready for clients to download
│   └── processed/  # Archived after processing
└── nodelists/
    └── <game_type>/
        └── <league_number>/
            └── BRNODES.<league>  # or FENODES.<league>
```

Nova Hub creates all subdirectories automatically on first use.

---

## dosemu2 Drive Mapping

Nova Hub generates a minimal `dosemu2` configuration on the fly for each
game type (one config shared across all leagues of that type):

```
<data_dir>/dosemu_configs/bre.conf
<data_dir>/dosemu_configs/fe.conf
```

**Default C: drive** is the dosemu2 default (`~/.dosemu/drive_c/`).  This is
where the game executable and its data files live.

> **Important:** Nova Hub does **not** remap the C: drive — it relies on
> dosemu2's default drive_c.  Each league's game installation must therefore
> live inside `~/.dosemu/drive_c/` (or whichever path dosemu2 maps to C:).

A temporary `PROCESS.BAT` batch file is written to the drive root for each
run.  It switches to `game_dos_path`, runs the configured command, then exits.

---

## config.toml Configuration

Global dosemu2 settings:

```toml
[dosemu]
dosemu_path = "/usr/bin/dosemu2"   # Full path to the dosemu2 binary
config_dir  = "./dosemu_configs"   # Where generated .conf files are stored
timeout     = 300                  # Seconds before dosemu2 is killed
```

Per-league settings — one section per league × game type:

```toml
[dosemu.<league_number>.<game_type>]
# Filesystem path to the game installation directory (Linux path)
game_folder = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555"

# Equivalent DOS path (used in the batch file's CD command)
game_dos_path = "C:\\bbs\\doors\\bre_555"

# Where the game reads inbound packets from (Linux path)
inbound_folder = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555/INBOUND"

# Where the game writes outbound packets to (Linux path)
outbound_folder = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555/OUTBOUND"

# DOS command to run game processing (relative to game_dos_path)
processing_command = "BRE.EXE PLANETARY /DETAILED"

# DOS command to generate scores files
scores_command = "BRE.EXE SCORES"

# DOS command to generate route-info (optional)
routeinfo_command = "BRE.EXE ROUTEINFO"

# DOS command to generate BBS info (optional)
bbsinfo_command = "BRE.EXE BBSINFO"

# Where score files (BBSLAND.ANS, etc.) are written
scores_folder = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555"
```

Replace `555` / `bre` with the appropriate league number and game type.

---

## Example: Two-League Setup

```toml
[dosemu]
dosemu_path = "/usr/bin/dosemu2"
config_dir  = "./dosemu_configs"
timeout     = 300

[dosemu.555.bre]
game_folder      = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555"
game_dos_path    = "C:\\bbs\\doors\\bre_555"
inbound_folder   = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555/INBOUND"
outbound_folder  = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555/OUTBOUND"
processing_command = "BRE.EXE PLANETARY /DETAILED"
scores_command   = "BRE.EXE SCORES"
scores_folder    = "/home/novahub/.dosemu/drive_c/bbs/doors/bre_555"

[dosemu.013.fe]
game_folder      = "/home/novahub/.dosemu/drive_c/bbs/doors/fe_013"
game_dos_path    = "C:\\bbs\\doors\\fe_013"
inbound_folder   = "/home/novahub/.dosemu/drive_c/bbs/doors/fe_013/INBOUND"
outbound_folder  = "/home/novahub/.dosemu/drive_c/bbs/doors/fe_013/OUTBOUND"
processing_command = "FE.EXE PLANETARY /DETAILED"
scores_command   = "FE.EXE SCORES"
scores_folder    = "/home/novahub/.dosemu/drive_c/bbs/doors/fe_013"
```

---

## Processing Flow in Detail

```
upload arrives
     │
     ▼
packets/inbound/<filename>   ← written by service API
     │
     ▼  (trigger_processing)
dosemu/<league>/<game>/inbound/<filename>   ← copied by ProcessingService
     │
     ▼  dosemu2 runs game
dosemu/<league>/<game>/outbound/<outfile>   ← game writes results
     │
     ▼  collect_outbound_packets
packets/outbound/<outfile>   ← moved here, DB record created
     │
     ▼  (clients poll /leagues/{id}/packets?unread=true and download)
```

---

## Nodelist Generation

Hub-side nodelists (`BRNODES.<league>`, `FENODES.<league>`) are generated from
the league membership database automatically after each processing run and can
also be triggered manually via the management API:

```
POST /management/api/v1/leagues/{league_id}/generate-nodelist
```

Generated files land in:

```
<data_dir>/nodelists/<game_type>/<league_number>/BRNODES.<league_number>
```

Clients download them via:

```
GET /service/api/v1/leagues/{league_id}/packets/BRNODES.<league_number>
```

### What a generated nodelist has to contain

The hub is node 1 of every league it runs, but it is not a client and has no
membership row, so it is written from `[hub]` config plus the league's own
`hub_fidonet_address` — which is per-league, because 013 addresses the hub as
`13:10/1` and 015 as `135:1/1`.

Line 1 of that entry carries the game's routing directive:

```
1 HOST 2 3 4
```

With no `route.cfg` present — and none of the production installs has one —
that line is the only thing telling the game where mail goes. A nodelist
without it is not a degraded nodelist, it is a broken one. The targets are the
league's member indices. A league that does not route through the hub sets
`hub_routes_mail = false` and gets a bare `1` instead; do not change this on a
running league without knowing which form its games expect.

Because a partial nodelist is worse than a stale one, generation **refuses to
write** when it cannot build the hub entry — `hub_fidonet_address` unset — and
leaves the previous file in place. The management endpoint returns 422 saying
so; the automatic post-run generation logs an error.

Files are written CRLF, atomically, to match the nodes.dat the games use.

### Backfilling an existing deployment

`hub_fidonet_address`, `hub_routes_mail` and the clients' city/state/country
start empty, so generation is blocked until they are filled in.
`backfill_nodelist_identity.py` reads them out of the nodes.dat each game is
already using rather than inventing them:

```bash
.venv/bin/python backfill_nodelist_identity.py           # dry run
.venv/bin/python backfill_nodelist_identity.py --apply
```

It does not touch `bbs_name`. Where the database and the file disagree on a
name, it says so and leaves both alone — that is a decision, not a migration.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| `No DOSEMU configuration found for league X` | Missing `[dosemu.X.bre]` or `[dosemu.X.fe]` section in config.toml |
| `Processing timed out` | Increase `[dosemu] timeout`; check dosemu2 logs in `<data_dir>/logs/dosemu/` |
| Game doesn't find inbound packets | Verify `inbound_folder` matches the path the game executable reads from |
| No outbound packets collected | Verify `outbound_folder` matches where the game writes output |
| dosemu2 exits with code 1 immediately | Check that the game executable path is correct and the DOS C: drive is set up |
