# Test environment spikes — findings

De-risking `TEST_ENVIRONMENT.md` before building the harness. This is the spike
phase's output: what the open questions turned out to be, with evidence.

Host: `novatest-hl` (Debian 13, dosemu2 2.0pre9 + fdpp + comcom64).
Everything below is from real runs on league **900** only. Installs live at
`~/.dosemu/drive_c/bbs/doors/b900n01` and `b900n02`; the scripts are in
`~/novatest-spikes/`.

## Corrections this phase makes to `TEST_ENVIRONMENT.md`

| The doc / plan said | Actually |
|---|---|
| a healthy dosemu log is 19–28 KB | that band is a 4-node league. A healthy 2-node run is 3.5–5 KB. Size scales with peer count; assert on phase markers instead |
| `Player List` proves a player played | it appears on a virgin game with zero turns. The player-traffic discriminators are `Message`, `Attack`, `Gooie Kablooie` |
| `libfaketime` is the primary date mechanism | it deadlocks dosemu2. `REDATE.COM` is the mechanism |
| the `TERM=dumb` log says "Your terminal lacks the ability to clear the screen" | it does not. The signature is a ~490 B log plus `<not executed on terminal>` |
| `full`/`scores`/`recon`/`planetary`/`outbound` are the maintenance sequence | only `PLANETARY` moves packets. `FULL` is the *player* path and blocks forever headless |
| provisioning is a file copy | plus a driven interactive `RESET`, and the install directory name must fit DOS 8.3 (see below) |

## Answers to the phase's seven questions

1. **Does headless BRE run as it does in production?** Yes (S1).
2. **How much traffic without a player?** Everything except player messaging — full
   bidirectional packet exchange, five data types (S2).
3. **What advances game time?** `REDATE.COM /iYYYYMMDD`; `libfaketime` does not work (S3).
4. **Is the `TERM=dumb` lever available, and does `script` mask the exit code?**
   Yes, and **yes — R-1 is confirmed live** (S4).
5. **Is fixture restore a true reset?** Yes; filenames and sequence numbers are
   identical across runs (S5).
6. **Is the game reachable as a player?** Yes, to the main menu and back out cleanly (S6).
7. **Are packet types reliably observable?** Yes, with `/DETAILED` (cross-cutting).

---

## Headline: the finding that changes the harness design

**BRE cannot traverse an absolute path through a directory whose name breaks DOS 8.3,
and it fails silently.**

The provisioner originally created `bre_900_n01` / `bre_900_n02` — 11 characters.
Those installs ran perfectly: `BRE PLANETARY` completed every phase, wrote its
backup, produced correctly-named outbound packets, exited 0, and left a
production-shaped transcript. They just never ingested a single inbound packet.

The reason is that the game reaches its own files by *relative* path (dosemu's `-K`
makes the install the current directory, so that always works), but reaches its
**inbound directory by the absolute path in `bbs.cfg` line 4**. Turbo Pascal's path
handling cannot walk `C:\BBS\DOORS\BRE_900_N02\INBOUND`, so `FindFirst` matches
nothing. There is no error: not on screen, not in `PROBLEMS.LOG`, not in the exit
code. The `■ Processing Incoming Data` phase marker still prints.

Proof: with nothing else changed, pointing line 4 at `C:\IN2` consumed all three
queued packets immediately and produced `900b0201.001` back to node 1.

Install directory names are now capped at 8 characters (`b900n01`).

Two consequences worth carrying forward:

- **This is the same failure class as the production `fe_015` R-3 bug.** That one is a
  *wrong* path (`c:\sbbs\fido\inbound`); this one is an *untraversable* path. Both
  present identically: healthy run, healthy log, healthy exit code, no packets moved.
- **The only reliable detector is non-consumption.** See the R-3 section below.

---

## Spike 0 — Provisioning two league-900 installs

Green. `provision_league.sh <node> <league> <name> <fido> [nodes]` is reproducible:
two consecutive full runs produced identical trees.

What the "copy, don't install" finding missed:

- `BREDATA -Y` regenerates `GAME/` (help text, events, news templates), **not** `DATA/`.
  Virgin game state comes from `BRE.EXE RESET`, which is interactive and is driven
  through `dosdrive.py`.
- **`RESET` deletes stray `.BAT` files from the game directory**, so `PROCESS.BAT` has
  to be written *after* the reset, not before.
- The install media ships a `route.cfg` containing `ROUTE * 2`. None of the three
  working production installs has a `route.cfg` at all — routing is handled by the
  `HOST` form in `BRNODES.DAT`. The provisioner now deletes it.

### The RESET prompt sequence is state-dependent, and the order is not what it looks like

Driving `RESET` needs a goal, not a script. The actual order is:

1. `Are you sure you want to reset the Game? (y/N)` → `Y`
2. **the Configuration Editor opens** → ESC to save and quit
3. *then* `Would you like this to be a league-wide reset? (Y/n)`

Step 3 appears **only on the node whose FidoNet address matches the `HOST` entry in
`BRNODES.DAT`** — node 01 sees it every time, node 02 never does. Early runs made this
look intermittent because interrupted runs left the install without donor data.

`dosdrive.py` gained a goal-directed `until(goal, answers)` primitive for this: state
the destination screen and how to answer whatever prompts show up on the way, each
answer firing at most once. That is more robust than an ordered expect-script against
a program that branches on its own data.

ESC still has to be *mashed* — dosemu treats a single ESC as the start of an escape
sequence and swallows it. Three presses at 0.25 s gaps got through on every run.

### Fixtures

`fixture.sh capture|restore|verify|list` — `tar --zstd` into `/srv/novatest/fixtures`,
with `inuse.flg` removed before capture and after restore. A capture/restore
round trip `diff -r`s clean. ~394 KB per install.

---

## Spike 1 — Headless run baseline

Green on both nodes. `BRE.EXE PLANETARY /DETAILED` exits 0, writes its backup,
rotates `TDYNEWS`→`YESNEWS`, updates `GAME.DAT`/`PLANET.BRE`, leaves no `inuse.flg`,
and emits every phase marker.

**Correction to the plan's success criterion.** The "healthy 19–28 KB log" band came
from a 4-node production league. A healthy 2-node league-900 run is **3.5–5 KB**. Log
size scales with the number of peers, so it is not a portable health signal. The
portable signal is the phase markers — specifically `■ Planetary Maintenance Complete`.

`script -c` was abandoned entirely in favour of `dosrun.py`, which drives dosemu on a
pty it owns. Two things that cost a lot of time and are now handled:

- the pty needs an explicit 80×25 winsize (`TIOCSWINSZ`) or DOS refuses to start;
- the transcript must be read until the **pty reports EOF**, not until the first child
  exits — dosemu's initial process can exit while a child is still writing, and
  breaking on `waitpid` truncates the log.

---

## Spike 2 — Traffic without a player

Green, and it reaches further than the plan assumed.

`loopback.sh <rounds> [cmds...]` moves each node's `OUTBOUND` into the other's
`INBOUND` and runs the command sequence. Three rounds, from pristine fixtures:

| round | n01 outbound | n02 outbound | consumed |
|---|---|---|---|
| 1 | `900b0102.001`, `brnodes.900` | `900b0201.001` | — |
| 2 | `900b0102.002` | `900b0201.002` | both |
| 3 | `900b0102.003` | `900b0201.003` | both |

- **Only `PLANETARY` moves packets.** `SCORES` builds the top-planet reports,
  `RECON` prints `Global recons created.`, and `OUTBOUND` re-runs the outbound half of
  planetary maintenance — none of the three produces or consumes a packet on its own.
- **`FULL` is not a maintenance command.** It is the player path (`BRE.DOC`: "combining
  BRE INBOUND, a player, ..."). Run headless it blocks forever on
  `Do you want ANSI Graphics? (Y/n)`. It belongs to Spike 6, not here.
- Filenames match `<league><game><src><dst>.<seq>` and the sequence advances by exactly
  one per direction per round.
- **Case: the game writes them lowercase** (`900b0102.001`), while the packets in the
  hub's own store are uppercase (`013B0201.964`). Worth confirming `is_packet_file()`
  and the hub's parser are case-insensitive.
- `brnodes.900` — the host distributing the nodelist — appears once, in round 1 only,
  and is not a `<league><game>...` packet. Anything walking an outbound directory has
  to expect it.

### Packet types, and a correction to the plan

`/DETAILED` is **required** for the per-item `Type: <x> <src>-> <dst>` lines. Without
it the run is identical but the packet contents are invisible to the parser. BRE
accepts the flag on `INBOUND`, `OUTBOUND` and `PLANETARY`.

The no-player type set observed over three rounds:

`Recon Update`, `Recon Request`, `Routing List`, `Configupdate`, **`Player List`**

**`Player List` is not a player-traffic discriminator.** The plan assumed it was — that
seeing it would prove a pilot had played. It appears in round 1 on a virgin game with
zero turns taken (an empty roster is still a roster). The discriminators that remain
for Spike 6 are `Message`, `Attack` and `Gooie Kablooie`.

(`Time Check` and `Dummy Data`, present in the production log, have not appeared in a
2-node league yet.)

### R-3 detector — confirmed

Deliberately broke `bbs.cfg` line 4 to `C:\BBS\FIDO\INBOUND` (the way `fe_015` is
broken in production) with a packet queued:

- dosemu exit code: **0**
- log size: 3602 bytes — squarely in the healthy band
- every phase marker present, including `■ Processing Incoming Data`
- `PROBLEMS.LOG`: **no new entries**
- the packet: **still sitting in `INBOUND`**

So the harness assertion cannot key on exit code, log size, phase markers or the
game's own error log. The two observables that do work:

1. the file placed in `INBOUND` is gone afterwards, and
2. the transcript contains at least one `DeCompress:` line (zero when broken).

---

## Spike 4 — The forced-failure lever, and R-1

Green, and **R-1 is confirmed live**.

| invocation | exit code | log size |
|---|---|---|
| `dosrun.py`, `TERM=dumb` | 0 | 4928 B (healthy) |
| `script -qc …`, `TERM=dumb` | **0** | 487 B (dead) |
| `script -qc …`, `TERM=linux` | 0 | 3685 B (healthy) |

- `dosrun.py` is immune because it pins `TERM` itself, so the outer `TERM=dumb` never
  reaches dosemu. To use the lever against it, pass `--term dumb`.
- **`script` exits 0 on a failed run.** The hub keys success on `script`'s exit code
  (`dosemu_runner.py`), so a dead run is recorded as `completed`. That is R-1, live.
- The dead log does **not** contain "Your terminal lacks the ability to clear the
  screen" as the plan expected. Its real signature is the ~490-byte size plus
  `<not executed on terminal>` in the `script` header.
- **There is a cheap fix.** `script` writes the truth into its own trailer:
  `Script done on … [COMMAND_EXIT_CODE="1"]`. The hub can either parse that or pass
  `script -e`, which makes `script` return the child's exit code.

---

## Spike 5 — Repeatability

Green for the invariants that matter.

The three-round loopback was run twice, the second time from restored pristine
fixtures. **Outbound filenames and sequence numbers were identical across both runs**
(`.001`/`.002`/`.003` in each direction, same node pairs, same round). Fixture restore
is therefore a true reset.

Compressed sizes are *nearly* stable — `Recon Update` compressed to 36 bytes in most
rounds and 35 in one. Size is a weaker assertion than presence; assert on the
`(direction, type, src, dst)` set, and treat sizes as informational.

---

## Cross-cutting — the transcript parser

`parse_dosemu_log.py` emits `{kind, phase, direction, type, src, dst, size_before,
size_after}` plus the `Outbound mail for <name> - Node <n> created.` records, with
`--json` and `--phases`. Validated against a real 4-node production log
(21 phases, 19 items, 3 outbound mails) as well as the league-900 runs.

Two traps that cost real time, both now handled:

- **Encoding is not fixed.** The production log is UTF-8 (`■` = `e2 96 a0`); our own
  transcripts are cp437. The parser tries UTF-8 then falls back to cp437.
- **Our transcripts interleave a `0x0F` (SI) byte between `■` and the phase text**;
  production's do not. Stripping C0 controls (except CR/LF/TAB) before matching took
  the phase count from 3 to 15 on our own logs.

Deduping on the semantic key `(phase, direction, type, src, dst)` was chosen over
replaying through `pyte`: BRE redraws the same region repeatedly, so naive line
counting overcounts, but the dedupe key is cheap and the record shape is exactly what
nova-hub would want to store. `pyte` is still used for *interactive* driving, where a
reconstructed screen is the point.

---

## Spike 3 — Faked game time

Green, but **the primary mechanism lost and the fallback won.**

### libfaketime does not work here

`faketime -f '+1d' … dosemu …` wedges the run. dosemu2 boots normally, BRE starts and
prints its overlay-manager banner, and then everything stops. No prompt, no error, no
progress — the transcript dies at 1.9–2.1 KB and `dosrun.py` reports `TIMEOUT`.

It is not the shift: **`+0d` wedges identically**, so `libfaketime` itself is the
problem, not the faked value. `FAKETIME_DONT_FAKE_MONOTONIC=1` does not help.

The likely mechanism is that `libfaketime` is not async-signal-safe (it takes a lock
inside its `clock_gettime` interposer) while dosemu2 reads the clock from its timer
signal handler — so the emulator deadlocks against itself on the first tick. Whatever
the precise cause, the negative result is reproducible and cheap to re-check.

### REDATE.COM wins

`REDATE.COM` hooks int 21h AH=2Ah (GET-DATE) only, which is exactly where Turbo
Pascal's `GetDate` goes, so BRE sees the faked date and nothing else does.

```
REDATE /iYYYYMMDD     install, return this fixed date  (prints "redate: successfully installed!")
BRE.EXE PLANETARY /DETAILED
REDATE /r             remove
```

Three consecutive game days on one install, then a deliberate repeat:

| invocation | `■ Running Daily Maintenance` |
|---|---|
| `/i20261001` | fires |
| `/i20261002` | fires |
| `/i20261003` | fires |
| `/i20261003` again | **skipped** — correct |

That last row is the control that makes the result meaningful: BRE is genuinely
tracking the faked date, not just running maintenance on every invocation.

Two questions from the plan, answered:

- **BRE 0.988 is y2k-safe.** It handles 2026 dates natively — the un-shifted baseline
  wrote `BACKUP/090926.dat` for 9 Sep 2026. The `90SDATE2` 30-year back-shift is for
  0.956 and is **not needed**; use real dates with `/iYYYYMMDD`.
- **The backup filename is `MMDDYY.dat`**, matching the US-style dates in
  `PROBLEMS.LOG`. Under `/i20260912` BRE wrote `091226.dat`.

The "advance one day" primitive for the harness is therefore an absolute date string,
not an offset — which is the better primitive anyway, since it does not depend on the
host clock. Note the backup file is only written on the first run after a reset; the
durable per-day assertion is the `■ Running Daily Maintenance` marker.

---

## Spike 6 — Reaching the game as a player

Green. Feasibility is settled, and further than the plan asked for.

Local mode needs only a `DOORFILE.SR` with **COM port `0`**:

```
TEST PILOT
1          ANSI? 1=yes
1          IBM-Characters? 1=yes
0          Page Length, 0=unknown
38400      Baud rate
0          COM port, 0=local
-1         Time left, -1=unlimited
TEST PILOT
```

Driven through `dosdrive.py`, `BRE.EXE FULL` walks: ANSI prompt → hints page →
`Name your Realm:` → empire confirmation → the ANSI title screen → its own Planetary
Maintenance pass → **the main menu**, rendered legibly through `pyte`:

```
Current version of BRE: v0.988
Game started on 9-9-2026
───────────────[Barren Realms Elite]────────────────
(1) Play Game             (8) Game Bulletins
(2) See Status            (9) InterPlanetary Ops
(3) See Scores            (A) Game Instructions
(4) See Today's News      (B) Help Database
(5) See Yesterday's News  (P) Preferences
(6) Read Messages         (0) Quit
(7) Send Messages
────────────────────────────────────────────────────
Choice>
```

Sending `0` quits cleanly: no `inuse.flg` left behind, and `FULL`'s outbound half ran
and produced `900b0201.001`. The status line confirms the player was registered —
`TEST PILOT │ A │ Test Realm`.

**`(6) Read Messages` and `(7) Send Messages` are the player-traffic generators** the
harness needs for the `Message` type and the `>>>UNKNOWN<<<` attribution check.

### What driving an interactive DOS game actually requires

Two lessons, both now baked into `dosdrive.py`:

- **Ordered expect-scripts are the wrong shape.** The prompts before a given screen are
  not a fixed sequence — they depend on game state, whether the player exists yet, and
  how much news there is to page through. `until(goal, answers)` states the destination
  and how to answer whatever appears, which survived every variation.
- **Answers must be repeatable.** Pagination prompts (`─»>Paused<«─`, `Continue? (Y/n)`)
  recur many times in one walk; firing each answer once wedges the run. They now
  re-fire, but only once the screen has actually changed since that answer was last
  used, so a prompt that never clears is not hammered.

One trap worth naming: **goal patterns match the help text too.** `Game Menu` and
`(Q)uit` both appear inside BRE's own "Helpful Hints" page, so the walk reported
success while still sitting at a name prompt. Anchor goals on something only the target
screen shows — here, `Choice>`.
