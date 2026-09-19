# Nova Hub — non-production test environment

Sketch for a from-scratch test league. Deliberately **not** recovered from
`bbs8-hl`: an unmaintained host of unknown provenance is a worse starting point
than a clean build, and the whole value of this environment is that its state is
known and reproducible.

**Hands off production.** `novahub-vtr` (hub), `bbs10-hl` (Linux BBS running
nova-client) and `bbs7-hl` (Windows BBS) are live. Nothing below touches them.

---

## 0. Corrections

This document was written before anything was built. The spike phase
(`TEST_ENVIRONMENT_SPIKES.md`) and the harness phase that followed disproved
several things asserted below. The rest of the document is left as written; this
table is what actually holds.

| Said here | What is true |
|---|---|
| A dosemu log over a byte-size floor means a healthy run (§4b, §5) | Transcript size scales with how many peers a league has: a 4-node league runs 19–28 KB where a healthy 2-node one runs 3.5–5 KB. Any absolute floor rejects small healthy leagues. Assert on the game's own completion line (`Planetary Maintenance Complete`) instead. A floor is still meaningful for the near-empty (~330–500 B) "dosemu never started" case, nothing more. |
| Install directory names are free (`bre_900`, §3) | **Names must be 8.3-clean.** The 16-bit Turbo Pascal the games are built with cannot traverse a path with a longer component. It does not error: the game completes, prints every phase marker, exits zero, and ingests nothing. Rig installs are named `b900n01`. |
| Sequence numbers advance by one per node pair, testable by repeating a round (§4b) | Half right. A second `PLANETARY` on the same game day emits nothing — daily maintenance runs once — so a plain loop proves nothing. But a node is **not** limited to one packet per day: `REQUEST` (recon request to every board, and a recon back) and `RECON` (recons only) queue traffic, and the next `OUTBOUND` packages it into a real packet with the next sequence number. Sequence progression is testable inside one day; no date change needed. |
| Direct routing falls out of the topology (§6.2) | With HOST routing in `BRNODES.DAT`, every node addresses node 1 and a node-to-node packet never appears. It needs the game's own `ROUTE 3 3` override in `ROUTE.CFG` (see `DOCS/ROUTE.SAM`). The hub relays such a packet byte for byte. |
| dosemu can present a faked date to DOS (§4a) | Not via `libfaketime`: it is not async-signal-safe, and dosemu2's timer signal handler deadlocks under it — even at `+0d`. Use the DOS-side TSR `REDATE.COM`, which hooks int 21h AH=2Ah. Also: `DATE /T` is invalid under comcom64 and aborts the batch, so the date cannot be read back from the shell — read it from a BACKUP filename or the daily-maintenance marker. |
| — | `BRE.EXE FULL` is the interactive player path and blocks forever headless on the ANSI prompt. Maintenance is `PLANETARY`. |
| — | `/DETAILED` is required for the per-item `Type: <x> <src>-> <dst>` lines. Without it the run is identical and what moved between nodes is invisible. |
| — | `script -c` returns **its own** exit status, always 0. Without `-e` a dead dosemu is recorded as a successful run — this is R-1, and it was live in both `nova-hub` and `nova-client`. |
| — | Handing dosemu a bare host path to the batch file remaps `C:` to that file's own directory, so the generated batch's `CD <game_dos_path>` no longer resolves and the game never runs — while dosemu exits 0 and the transcript looks ordinary. Use `-K <dir> -E <name>`, which leaves `C:` alone. See §9. |
| — | The hub's data dir and the game folders are separate config paths and need not share a filesystem, so moves between them must be `shutil.move`, not `Path.rename`. |
| Restoring a fixture makes a run reproducible (§5) | **Only on the day the fixture was captured.** The games number packets by game day, so a fixture restored the next day is byte-identical on disk and one day older in `GAME.DAT` — its next packet is `.002`. `fixtures.stale()` detects this and `run.sh` rebuilds. |
| — | BRE writes its whole `DATA/` at RESET; **FE does not**. `planet.fe`, `routes.dat` and the rest arrive on the first `PLANETARY` run, which is also game-day one and emits real packets. FE fixtures are therefore captured straight after RESET. |
| — | One `process_batch()` is **one `ProcessingRun`** even when it spans several leagues or games. The separation is in the `(game_type, league_id)` grouping and in per-file `league_id`, not in the run count. |
| Sequence numbers are dense, so any unseen number is a lost packet (the hub's gap detector) | **They are not.** Each new game day consumes *two* sequence numbers and writes one file at the second — `.002`, `.004`, `.006` on three consecutive days. Forced traffic (`REQUEST` + `OUTBOUND`) advances by one, so a route's stride depends on what is driving it. Production had **705 gap alerts, all unresolved**, gap=1 the largest bucket. Fixed 2026-09-12: the detector now learns each route's own stride. See `ROLLOUT_PLAN.md` card B-4. |
| A healthy node emits a packet every game day | Only while it has something to say. An idle league produces on days one, two and three and then **nothing** — maintenance still runs and still prints its completion marker on days four to six. Silence is not a fault, and a "packet a day" health check would be the gap detector's mistake all over again. Generating longer runs of traffic needs player activity. |
| — | Player activity is now available: `rig/player.py` drives `BRE.EXE FULL` through `dosdrive`, taking turns and sending interplanetary mail. `node.run()` still refuses `FULL` — that refusal is about *unattended* runs and remains correct. See §10. |
| — | **Duplicate user checking is a league setting** — page 2 of the configuration editor during RESET, and changeable mid-league. While it is on, BRE refuses a BBS user name already playing on another board: `Duplicate User Found on BBS #2 ... you cannot join this game.` There is no prompt to get past; the session simply never reaches the main menu. The rig leaves the setting at its default and gives each node its own name via `player.door_user()`, which is correct either way. |
| — | The status screen **omits a line rather than printing a zero**: a realm holding no cash has no `Gold:` line, and an empty account has no `Bank:`. Absent means zero for those two; for anything else a missing field means the parse failed. |
| — | BRE **banks the day's gold automatically** at the end of a turn, so a realm that has just played reads `Gold: 0` with a full account. Any "is this realm broke?" logic has to read gold *plus* bank. |
| — | `(7) Send Messages` on the main menu is **local to the planet** — it asks `(A-Y,Z=All) Send to:` and never leaves the board. Interplanetary mail, the kind that becomes a packet, is `(9) InterPlanetary Ops` → `(7) Send Message` → a scope, of which `(3) All Planets` needs no planet number. |
| — | `(9)` → `(1) View IPScores` is a *menu of reports*, not a listing. Only the `Top Players by ...` reports name individual realms; the `Top Planets by ...` ones are per-board totals. |
| — | Re-running while a packet is still pending in OUTBOUND **rewrites it in place and keeps its number** — 176 → 232 → 288 bytes under one filename. A packet sitting in a game outbound folder is not a finished artefact; its contents change until something collects it. |

---

## 1. Topology

One new Linux VM on the tailnet — call it `novatest-hl` — hosting the hub *and*
all simulated BBS nodes as separate unix users. Separate users (not containers)
because each node needs its own `~/.dosemu/drive_c`, and dosemu is happier with a
real home directory than with a container's.

```
novatest-hl
├── user: novahub-t     hub          BBS index 01   :8000
├── user: node02        nova-client  BBS index 02   own drive_c, own games
├── user: node03        nova-client  BBS index 03   own drive_c, own games
└── user: node04        nova-client  BBS index 04   own drive_c, own games
```

**Why three client nodes, not one.** Two nodes only ever exercise
`node → hub → node`. A third is the minimum that exercises the routing the hub
actually exists to do:

- **02 → 01** — packet addressed to the hub itself (the common case).
- **02 → 03** — direct-routed packet the hub must relay without processing
  (`dest_bbs_index != hub_index` — the branch at `processing_service.py:341`).
- **04** — a node that is deliberately *idle* most of the time, so sequence-gap
  detection and "client hasn't downloaded yet" (the `queued` status, card F-3)
  have somewhere to happen.

A fourth node is only worth adding if you want to test nodelist ordering.

## 2. League numbering — the single most important isolation rule

**Test leagues must use numbers that can never collide with production.**
Production runs `013B`, `014B`, `015B`, `015F`. Reserve a block far away:

| League | Game | Purpose |
|---|---|---|
| `900B` | BRE | Primary happy-path league |
| `900F` | FE | Same nodes, second game type |
| `901B` | BRE | Second BRE league — exercises the multi-league bug (card B-1) |

`901B` matters: B-1 only reproduces when **two leagues of the same game type**
exist. Production has that today; the test environment must too, or the 0.2.0
release ships untested against the exact bug it fixes.

This numbering is the primary safety barrier. A test packet that somehow reached
the production hub would be rejected as an unknown league rather than being fed
into a real game.

## 3. Game installations

Each node needs its own BRE/FE install per league — DOS games keep all state in
their own directory, so there is no sharing.

```
~/.dosemu/drive_c/bbs/doors/bre_900/
~/.dosemu/drive_c/bbs/doors/fe_900/
~/.dosemu/drive_c/bbs/doors/bre_901/
```

**Build a pristine fixture tarball per (game, league, node) role**, captured
immediately after a clean install + configuration and *before* any packets are
processed. Store them outside the game tree:

```
/srv/novatest/fixtures/bre_900_node02_pristine.tar.zst
```

The fixtures are the foundation of repeatability. Game state is mutable and
cumulative — empires grow, turns advance, `game.dat` changes every run — so
without a restore-to-known-state step, no test is repeatable and no failure is
reproducible.

**Capture `BBS.CFG` correctness in the fixture build**, since a stale path there
is precisely what silently broke FE in production (card R-3). Verify line 4
points at that install's own `INBOUND` before tarring.

## 4. The two hard problems

Everything above is routine. These two are what make BRE/FE testing genuinely
awkward, and they need deciding before writing any harness code.

### 4a. Game time

BRE and FE run **daily maintenance** keyed to the DOS system date, and a lot of
interesting behaviour (dead empires, elections, yearly events — all visible in
the FE log) only fires on a date rollover. Real time is far too slow to test on.

dosemu can present a faked date to DOS independently of the host clock. The
harness should treat "advance the game by one day" as a first-class operation, so
a multi-day scenario runs in seconds. This wants proving out early with a
throwaway spike — if faked dates turn out not to hold across a dosemu run, the
whole multi-round test design has to change shape.

### 4b. Determinism

These games contain randomness (combat, events). Byte-comparing outputs across
runs will not work. Assert on **structural invariants** instead:

- a packet placed in `INBOUND` is consumed (the file disappears)
- outbound packets appear with correct `<league><game><src><dst>.<seq>` names
- sequence numbers advance by exactly one per node pair
- the dosemu log contains the game's own completion line (**not** a size floor —
  see §0)
- a packet addressed to a third node is relayed, not processed

## 5. What the harness must assert

Framed as: *would this have caught the three bugs found on 2026-07-24?*

| Assertion | Catches |
|---|---|
| dosemu log contains the game's completion line | **R-2** (`TERM=dumb`, 330-byte log) |
| forced dosemu failure ⇒ run `failed`, packets stay unprocessed, files stay in `inbound/` | **R-1** (false success) |
| every packet copied to game `INBOUND` is consumed by the game | **R-3** (stale `BBS.CFG`) |
| with two BRE leagues, each run's output files attach to the correct league | **B-1** |
| service REST responses match a recorded contract | third-party client compatibility |

The forced-failure case is easy now: set `term = "dumb"` in the test hub's
`config.toml` and dosemu reproduces the exact production outage on demand.

## 6. Suggested test scenarios

1. **Single round, single league** — 02 uploads, hub processes, 02 downloads.
2. **Direct routing** — 02 sends to 03; hub relays without processing.
3. **Multi-league same game** — 900B and 901B both have traffic in one run (B-1).
4. **Both game types** — 900B and 900F in one run; verify no cross-contamination
   (this is where the FE-vs-BRE confusion of 2026-07-24 would have surfaced).
5. **Sequence gap** — withhold a packet, assert a `SequenceAlert`, then deliver
   it late and assert resolution.
6. **Duplicate replay** — re-send an already-processed packet. Both games detect
   duplicates, so this must be a no-op — which also makes replay a safe recovery
   tool in production.
7. **dosemu failure** — `term = "dumb"`; assert no data loss (R-1).
8. **Multi-day** — advance game time several days; assert daily maintenance runs.

## 7. Build order

1. VM + dosemu2/FDPP, matching prod's version (`2.0pre9`).
2. Hub install, league `900B` only, one client node. Get one round green by hand.
3. Fixture capture + restore tooling. Nothing is repeatable until this exists.
4. pytest harness wrapping steps 2–3; scenarios 1 and 7 first — they are the
   regression tests for the outage that prompted all this.
5. Add `900F`, then `901B`, then nodes 03/04 and the remaining scenarios.
6. Wire into CI as a nightly + pre-release gate. Too slow for per-commit.

Stop after step 4 if time is short: scenarios 1 and 7 alone would have caught the
production outage, and they are ~20% of the total effort.

## 8. Out of scope for now

`game_runner.py` claims Windows/ntvdm16 support that nothing verifies. Testing it
needs a Windows VM (`bbs7-hl` is production — do not use it). Until a dedicated
test VM exists, mark the Windows path explicitly unsupported in the README rather
than leaving an untested claim standing.

---

## 9. What was built

The rig lives in `nova-hub/tests/live/`, runs on `novatest-hl`, and is driven by
`tests/live/run.sh`. `rig/` holds the machinery (provisioning, fixtures, the pty
runner, the transcript parser, the test hub); one `test_*.py` per scenario.

Scenarios 1, 2, 3, 4, 6 and 7 are implemented, plus the R-3 inbound-path
detector. **21 tests**, green twice in a row from restored fixtures.

`900F` (scenario 4) landed on 2026-09-11: Falcon's Eye across all four nodes,
covering the round trip, the completion marker from FE itself, R-3 in the game it
actually happened to, and two different games in one batch — the `015B`/`015F`
pairing that exists in production, where both share a league number and differ
only by game type.

Scenario 5 (sequence gap) landed on 2026-09-11, once it turned out not to need a
date change at all: `REQUEST`/`RECON` + `OUTBOUND` forces extra packets within a
single day, so `.001 .002 .003` come from a real game and the gap detector is fed
a genuine missing packet rather than a hand-written filename.

Deferred, and still worth doing: multi-day (8), player-driven traffic, and CI
wiring. Multi-day is unblocked — `REDATE` settles the mechanism — so it is a
scenario to write, not a risk.

### Fixtures expire

A pristine fixture is a virgin game *on its capture date*. The games derive a
packet's sequence number from how many game days have passed since `RESET`, so a
fixture restored the next day gives a byte-identical tree and a game that is one
day older: its next packet is `.002`, and every scenario asserting `.001` fails
on arithmetic for reasons that look nothing like the cause.

This cannot be fixed by copying files. The start date lives inside `GAME.DAT`,
and the only supported way to move the date a game sees is `REDATE.COM` — a TSR
that would have to load ahead of every invocation, including the ones the hub's
own product code launches, which the rig does not get to write.

So the fixture is rebuilt instead. `rig.fixtures.stale()` reports any fixture not
captured today, `conftest.py` turns that into a skip that names the cause, and
`run.sh` rebuilds automatically before running. Rebuilding all ten installs takes
a few minutes and happens at most once a day.

### A live production regression

The hub's dosemu invocation form stopped working somewhere between the March 2026
packages and the July 2026 ones. The bare-path form now remaps `C:` to the batch
file's own directory. Since `data_dir` sits outside `~/.dosemu/drive_c`, the
generated batch's `CD C:\bbs\doors\<game>` no longer resolves, the game is never
launched, dosemu exits 0, and the run is recorded successful.

Measured on `novahub-hl`, which carries the same dosemu packages as production
(`dosemu2 2.0~pre9-10228-0aeb4d174+202607270023`, `fdpp 1.10-10002`, `comcom64
0.4-0~202607221057`), by running the hub's own `DosemuRunner` against a 900-block
scratch league with a stand-in for `BRE.EXE`:

| invocation | run status | game actually ran |
|---|---|---|
| bare host path (pre-fix) | `success`, exit 0 | **no** |
| `-K <dir> -E <name>` | `success`, exit 0 | yes |

A March 2026 transcript still on that host shows the old behaviour on the same
config — `Changing to: C:\bbs\doors\bre_013` followed by a successful `CD` and
BRE's overlay manager starting — so this is a package-level change, not a config
drift.

**This is not a future risk; it is the current state of any hub on these
packages.** The completion-marker check added in this phase turns it from silent
into a failed run, but only for leagues that declare a marker.

`novahub-vtr` has deliberately not been inspected or touched.

---

## 10. Playing as a player

`tests/live/rig/player.py`. Everything else in the rig drives the games the way
the hub does — unattended maintenance — which cannot produce the two things a
league actually carries once people are in it: a score that changed because
somebody took a turn, and a message somebody wrote.

```python
from rig import player

# do exactly this
player.visit(LEAGUE, NODE02, realm="Node Two", turns=1,
             ip_messages=["hello"])

# or hand the wheel to the decision module
player.visit(LEAGUE, NODE02, agent_rounds=3, activity_weight=1.0)
```

`visit()` returns the parsed status before and after, so a scenario can assert on
a **score change** rather than merely on a packet appearing, plus the decisions
taken and the path of the JSONL log they were written to.

### Driving an interactive DOS game: what it actually takes

`dosdrive.py` already had the pty and the pyte screen. Three things had to be
added on top, each found the hard way:

* **Match the live prompt, not the history.** `dosdrive.until()` matches its
  answers against `screen_text() + raw_text()`, and `raw_text` is append-only. A
  prompt seen once therefore matches forever, and its answer re-fires into
  whatever is on screen later. On the provisioner's short `RESET` walk that never
  shows; on a player walk it typed `Y` into the realm-name field and wandered off
  into the buy menu. `player.Player.prompt` is the last non-empty screen line
  (skipping the permanent `F2=Extra Information` status bar), and answers are
  matched against that alone.
* **Never press bare `<CR>` to "settle" a screen.** At BRE's `Choice>` an empty
  line selects `(1) Play Game`, and the walk disappears into the daily turn
  sequence.
* **An answer may re-fire only once the whole screen has changed.** Keying that
  on the prompt *line* instead stalls forever: every page of BRE's instructions
  ends in the same `Continue? (Y/n)`.

### The daily turn

One press of `(1)` plays a turn and then asks `Do you wish to continue?`; `Y`
runs straight into the next one, so a whole session is a single trip through Play
Game with that one answer counting down. Everything in between has a default, and
taking every default is the do-nothing turn: Diplomacy → Status → Payment/Food
Market → Covert Ops → Bank → Spending → Attacks → Trading → IP Ops → Messages,
quitting each submenu with `0` and taking `<CR>` at every `How much will you
give? (x; y)`.

A do-nothing turn still moves the score — production runs and taxes are
collected — which is the point: a nominal score change is a real thing to send to
another host.

Quitting through `(0)` is not politeness. `FULL`'s outbound half runs on the way
out and is what packs the day's mail into a packet; a killed session leaves
`inuse.flg` behind, and every later run then exits 1 having printed nothing about
why.

### Reading interplanetary mail

**Interplanetary messages are never shown under `(6) Read Messages`.** They
appear at the **start of play** — press `(1)`, and after any local messages the
game shows each one in a reader:

```
┌──────────────────────────────────────────────────09/19/2026  06:15:42─────
│ Message From: Node Two on Test Node 02
│ Message To  : ABCDEFGHIJKLMNOPQRSTUVWXY
├─────═══════───────────────────────────
│ RIG PLAYER MESSAGE ONE
[R]  Reply, [D]  Delete, [I]  Ignore, or [Q]  Quit>
```

That prompt looks nothing like a menu, so a walk that only knows about `Choice>`
hangs on it forever. `player` answers `Q`, which leaves the mail alone and closes
the reader; `Player.turn_text` has already captured what was shown, so a scenario
asserts on that.

This is why `test_the_score_and_message_arrive_at_the_other_node` has node 1 play
a turn rather than just read its menus: playing is the only way to see the mail.

### If a walk does hang

`player.visit()` clears `inuse.flg` and reports the tail of the transcript rather
than letting the timeout propagate. Without that, one unanswered prompt fails
every later test on that install — the game's mutex outlives the killed session,
and the next run exits 1 having printed nothing about why.
