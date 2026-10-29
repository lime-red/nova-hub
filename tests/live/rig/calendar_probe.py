"""Probe: is the emission schedule two metronomes rather than an irregular cycle?

The recorded BRE finding was "irregular and not reducible to a simple rule",
with a 35-day cycle and delta pattern [5,2,6,2,5,4,3,6,6,3,4]. But 35 = 5 x 7,
and the day-gaps [4,1,5,1,4,3,2,5,5,2,3] fit the union of two fixed periodic
series exactly:

    emit when (ordinal mod 5 == 1) or (ordinal mod 7 == 6)   [= every Saturday]

That reproduces all three measured windows (the 20-day one, Dec 1-6 and
Jan 1-6) with no exceptions, and explains the "failures" of a mod-5 rule --
Nov 7 and Nov 14 -- as members of the weekly series. 11 packets per 35 days is
7 + 5 - 1 overlap, and lcm(5,7) = 35 is the whole of the "cycle".

So this plays a chosen window, records which game days emit, and fits
(a mod p) or (b mod q) over small p and q. It takes a league so the same
question can be put to Falcon's Eye, which is unmeasured.

Run from tests/live:

    sudo -u novahub-t env NOVATEST_RIG=1 /srv/novatest/venv/bin/python \
        rig/calendar_probe.py <league> <node> <start YYYYMMDD> <days> [forced]

Measured answers, 2027-03 windows on novatest-hl:

    900B  (ordinal mod 5 == 1) or (ordinal mod 7 == 6, i.e. Saturday)
          11 packets per 35 days; unique fit over all p, q <= 10
    900F  (ordinal mod 4 == 2)
          1 packet per 4 days; every fit returned is this one restated

Pass a forced count to drive it under load instead, which masks the schedule
(every day emits) but checks the consumption law on several packets a day.

Immortal Barons (a league id ending I) is a native program, so it takes a
different shape: a throwaway league of <boards> boards built in a temporary
directory, no fixtures and no node users, relayed the way the hub relays it,
and the date moved with IB_GAME_DATE (IB's own test clock) rather than
REDATE.COM. The last argument is -maint runs per game day, each board in turn:

    /srv/novatest/venv/bin/python rig/calendar_probe.py 900I <boards> <start> <days> [runs]

Measured answers, novatest-hl, IB e9d34f2, 2026-10-28:

    900I  every -maint run emits, not every game day: the travel-time probe
          goes to every other board on every run (IB #287), and the
          Coordinator rebroadcasts bulletins, league config and roster each
          time. Per run, the Coordinator sends 3 broadcasts + one packet to
          each member; a member sends one to each other board. A second run on
          the same day emits the same again.
          Sequence numbers are a plain per-board counter: the k-th packet is
          k, with no day burn.
"""
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

_LIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_LIVE))
sys.path.insert(0, str(_LIVE.parent.parent))

from rig import node as node_rig
from rig.layout import IB_BINARY, LEAGUES, NODES, VENV_PYTHON

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def _restore(league):
    for index in league.members:
        node = NODES[index]
        prefix = [] if node.user == getpass.getuser() else ["sudo", "-u", node.user, "--"]
        subprocess.run(
            [*prefix, str(VENV_PYTHON), "-c",
             "import sys; sys.path.insert(0, '.'); "
             "from rig import fixtures; from rig.layout import LEAGUES, NODES; "
             f"fixtures.restore(LEAGUES[{league.league_id!r}], NODES[{index}])"],
            cwd=str(_LIVE), capture_output=True, text=True, check=True,
        )


def _lines(log):
    text = _ANSI.sub(b" ", log.read_bytes()).decode("latin-1")
    out = set()
    for raw in re.split(r"[\r\n]+", text):
        s = " ".join(raw.split())
        if 6 <= len(s) <= 70 and not s.startswith("C:"):
            out.add(s)
    return out


def fit(ordinals, emitted, first_ordinal):
    """Find every (p, a, q, b) whose union reproduces the observed emissions.

    The first game day after a restore always emits (the opening packet), so it
    is excluded from the comparison rather than allowed to prop up a bad fit.
    """
    obs = {o for o, e in zip(ordinals, emitted) if e} - {first_ordinal}
    pool = [o for o in ordinals if o != first_ordinal]
    hits = []
    for p in range(2, 11):
        for a in range(p):
            single = {o for o in pool if o % p == a}
            if single == obs:
                hits.append((p, a, None, None))
            for q in range(p, 11):
                for b in range(q):
                    if {o for o in pool if o % p == a or o % q == b} == obs:
                        hits.append((p, a, q, b))
    return hits


def main(league_id, node_index, start, ndays, forced):
    league = LEAGUES[league_id]
    node = NODES[node_index]
    prefix = f"{league_id[:3]}{league_id[3].lower()}{node_index:02x}01.".lower()

    _restore(league)
    first = date(int(start[:4]), int(start[4:6]), int(start[6:]))

    ordinals, emitted, numbers, per_day, marks = [], [], [], [], {}
    for n in range(ndays):
        d = first + timedelta(days=n)
        tag = f"cal{n}"
        log = node_rig.run(league, node, "PLANETARY", tag=tag, date=d.strftime("%Y%m%d"))
        got = [name for name, _ in node_rig.take_outbound(league, node)
               if name.lower().startswith(prefix)]
        for k in range(forced):
            node_rig.force_packet(league, node, "REQUEST", tag=f"{tag}f{k}",
                                 date=d.strftime("%Y%m%d"))
            got += [name for name, _ in node_rig.take_outbound(league, node)
                    if name.lower().startswith(prefix)]
        ordinals.append(d.toordinal())
        emitted.append(1 if got else 0)
        per_day.append(len(got))
        numbers.extend(int(g.rsplit(".", 1)[1]) for g in got)
        marks[n] = _lines(log)
        print(f"  day {n+1:2d}  {d:%a %Y-%m-%d}  packets={len(got)}  {got}", flush=True)

    print()
    print(f"league {league_id} node {node_index:02d}, {ndays} game days from {first}")
    print(f"emission pattern : {emitted}")
    print(f"packets per day  : {per_day}")
    print(f"numbers          : {numbers}")
    if numbers:
        print(f"number deltas    : {[b-a for a,b in zip(numbers, numbers[1:])]}")

    # the law, per packet: k-th number == game days elapsed + k
    days_of = [i+1 for i, c in enumerate(per_day) for _ in range(c)]
    bad = [(k, day, num) for k, (day, num) in enumerate(zip(days_of, numbers), 1)
           if day + k != num]
    print(f"law k-th==days+k : {'HOLDS for all %d' % len(numbers) if not bad else 'MISMATCH %s' % bad}")

    print()
    hits = fit(ordinals, emitted, ordinals[0])
    if not hits:
        print("NO (p,a)/(q,b) union in 2..10 reproduces this. Not two metronomes.")
    for p, a, q, b in hits:
        if q is None:
            print(f"FIT: ordinal mod {p} == {a}  (single series)")
        else:
            wk = ""
            if p == 7 or q == 7:
                per, res = (p, a) if p == 7 else (q, b)
                wk = f"   [the mod-{per} series is every "\
                     f"{date.fromordinal(next(o for o in ordinals if o % per == res)):%A}]"
            print(f"FIT: (ordinal mod {p} == {a}) or (ordinal mod {q} == {b}){wk}")

    emit_days = [n for n, e in enumerate(emitted) if e and n > 0]
    quiet_days = [n for n, e in enumerate(emitted) if not e]
    if emit_days and quiet_days:
        only_emit = set.intersection(*(marks[n] for n in emit_days)) - set.union(*(marks[n] for n in quiet_days))
        only_quiet = set.intersection(*(marks[n] for n in quiet_days)) - set.union(*(marks[n] for n in emit_days))
        print(f"\non every emitting day and no quiet day ({len(only_emit)}):")
        for s in sorted(only_emit)[:25]:
            print(f"   + {s}")
        print(f"on every quiet day and no emitting day ({len(only_quiet)}):")
        for s in sorted(only_quiet)[:25]:
            print(f"   - {s}")


_KEY = re.compile(r"\b[0-9a-f]{64}\b")
_IB_NAME = re.compile(r"L(\d{3})-([0-9a-z]+)-([0-9a-z]{13})-([0-9a-z]+)\.brp", re.IGNORECASE)


def _ib(data, *args, date_=None):
    env = dict(os.environ)
    env.pop("IB_CLOCK_OFFSET", None)
    if date_:
        env["IB_GAME_DATE"] = date_.isoformat()
    r = subprocess.run([str(IB_BINARY), *args, "-data", str(data)], env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def ib_league(root, number, boards):
    """Boards 1..n, board 1 the Coordinator, signed, on one roster."""
    dirs = {i: root / f"b{i}" for i in range(1, boards + 1)}
    names = {i: f"Test Board {i:02d}" for i in dirs}
    for i, d in dirs.items():
        d.mkdir()
        _ib(d, "-ibbs-reset", "-board-id", names[i])
        (d / "bbs.cfg").write_text(
            f"BoardID {names[i]}\nLeagueNumber {int(number)}\nGameInbound inbound\nGameOutbound outbound\n")
    coord = _KEY.search(_ib(dirs[1], "-gen-coord-key")[1]).group(0)
    roster = []
    for i, d in dirs.items():
        key = _KEY.search(_ib(d, "-gen-board-key")[1]).group(0)
        if i > 1:
            _ib(d, "-coord-key", coord)
        # As the hub writes it: CRLF, the key on a seventh line, no HOST lines
        roster += [str(i), names[i], f"{number}:{number}/{i}", "C", "S", "X", key, ""]
    for d in dirs.values():
        (d / "ibnodes.dat").write_bytes("\r\n".join(roster).encode())
    return dirs


def ib_relay(dirs, sender):
    """Move one board's outbound to its recipients, as the hub does: by the
    destination in the name, 0 meaning every board but the sender."""
    sent = []
    for f in sorted((dirs[sender] / "outbound").glob("*.brp")):
        m = _IB_NAME.fullmatch(f.name)
        dst = int(m.group(4), 36)
        for i, d in dirs.items():
            if i != sender and dst in (0, i):
                shutil.copy(f, d / "inbound" / f.name)
        sent.append((int(m.group(3), 36), dst))
        f.unlink()
    return sent


def ib_main(league_id, boards, start, ndays, runs):
    first = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    root = Path(tempfile.mkdtemp(prefix=f"ib{league_id}."))
    try:
        dirs = ib_league(root, league_id[:3], boards)
        numbers = {i: [] for i in dirs}
        per_run = []
        for n in range(ndays):
            d = first + timedelta(days=n)
            for run in range(1, runs + 1):
                row = []
                for i in dirs:
                    rc, out = _ib(dirs[i], "-maint", date_=d)
                    sent = ib_relay(dirs, i)
                    numbers[i] += [seq for seq, _ in sent]
                    refused = len(re.findall(r"refused", out, re.IGNORECASE))
                    row.append(len(sent))
                    dests = " ".join("all" if dst == 0 else str(dst) for _, dst in sent)
                    print(f"  {d:%a %Y-%m-%d} run {run} board {i}  rc={rc}  "
                          f"sent={len(sent)} [{dests}]" + (f"  REFUSED x{refused}" if refused else ""),
                          flush=True)
                per_run.append(row)

        print()
        print(f"league {league_id}, {boards} boards, {ndays} game days from {first}, {runs} run(s) a day")
        print(f"packets per run, by board : {sorted(set(map(tuple, per_run)))}")
        for i, nums in numbers.items():
            law = nums == list(range(1, len(nums) + 1))
            print(f"board {i}: {len(nums)} packets, numbers 1..{len(nums)} "
                  f"{'in order, no gaps' if law else 'NOT a plain counter: %s' % nums}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    if sys.argv[1].upper().endswith("I"):
        ib_main(sys.argv[1].upper(), int(sys.argv[2]), sys.argv[3], int(sys.argv[4]),
                int(sys.argv[5]) if len(sys.argv) > 5 else 1)
    else:
        main(sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]),
             int(sys.argv[5]) if len(sys.argv) > 5 else 0)
