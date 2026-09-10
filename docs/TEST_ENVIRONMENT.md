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
| Sequence numbers advance by one per node pair, testable by repeating a round (§4b) | A node emits **one packet per game day**, not per run. A second `PLANETARY` on the same day emits nothing at all, so sequence progression cannot be exercised by looping — it needs a date change, and belongs to the multi-day scenario. |
| Direct routing falls out of the topology (§6.2) | With HOST routing in `BRNODES.DAT`, every node addresses node 1 and a node-to-node packet never appears. It needs the game's own `ROUTE 3 3` override in `ROUTE.CFG` (see `DOCS/ROUTE.SAM`). The hub relays such a packet byte for byte. |
| dosemu can present a faked date to DOS (§4a) | Not via `libfaketime`: it is not async-signal-safe, and dosemu2's timer signal handler deadlocks under it — even at `+0d`. Use the DOS-side TSR `REDATE.COM`, which hooks int 21h AH=2Ah. Also: `DATE /T` is invalid under comcom64 and aborts the batch, so the date cannot be read back from the shell — read it from a BACKUP filename or the daily-maintenance marker. |
| — | `BRE.EXE FULL` is the interactive player path and blocks forever headless on the ANSI prompt. Maintenance is `PLANETARY`. |
| — | `/DETAILED` is required for the per-item `Type: <x> <src>-> <dst>` lines. Without it the run is identical and what moved between nodes is invisible. |
| — | `script -c` returns **its own** exit status, always 0. Without `-e` a dead dosemu is recorded as a successful run — this is R-1, and it was live in both `nova-hub` and `nova-client`. |
| — | Handing dosemu a bare host path to the batch file stopped working on the July-2026 dosemu2/fdpp packages: DOS boots, exits 0, and the batch never runs. Use `-K <dir> -E <name>`. See the warning in §9. |
| — | The hub's data dir and the game folders are separate config paths and need not share a filesystem, so moves between them must be `shutil.move`, not `Path.rename`. |

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

Scenarios 1, 2, 3, 6 and 7 are implemented, plus the R-3 inbound-path detector.
Deferred, and still worth doing: `900F` (scenario 4), the sequence gap (5),
multi-day (8), player-driven traffic, and CI wiring. Multi-day is unblocked —
`REDATE` settles the mechanism — so it is a scenario to write, not a risk.

### A production regression to watch

The hub's dosemu invocation form silently stopped working between the March 2026
packages and the July 2026 ones (`dosemu2 2.0~pre9-10228`, `fdpp 1.10-10002`,
`comcom64 0.4-0~202607221057`). On the new packages the bare-path form boots DOS,
exits 0, and never runs the batch at all.

Any hub still on the old form will, after a routine `apt upgrade`, process every
packet by booting dosemu, doing nothing, and recording the run as successful. The
completion-marker check catches it if the league declares one; without that, it is
invisible. `novahub-vtr` has deliberately not been inspected.
