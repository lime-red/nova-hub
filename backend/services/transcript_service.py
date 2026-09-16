"""Read a BRE/FE dosemu transcript for what actually moved between nodes.

The hub already stores every run's full transcript in `ProcessingRun.dosemu_log`,
and with `/DETAILED` in the processing command that transcript names each item
that crossed the wire -- its type, and which node sent it to which:

    DeCompress:  Old:  56  New:1448  %: 96.1%  Type: Recon Update   2-> 1
    Compress: Old: 1448  Size:   36  %: 97.5%  Type: Configupdate   1-> 2
    Outbound mail for SJ Team 1 - Node 2 created.

Not the contents -- the games do not reveal those -- but enough to see the shape
of the traffic and judge it by eye. That judgement is the point. A missing
sequence number has no single meaning (the games burn one at each game-day
rollover, so one lone gap a day per route is normal and indistinguishable from a
real loss), which makes it a poor thing to ring a bell about and a fine thing to
show someone who knows what the league should look like.

Two transcript quirks that any reader has to survive, both found in real logs:

* **The screen is repainted.** In dosemu's vga mode the emulated display is
  rewritten many times a run, so the same line appears repeatedly and counting
  raw matches overcounts wildly. Records are de-duplicated on their full content.
* **Repaints run records together.** A painted line can read
  `...Type: Recon Update   4-> 1DeCompress:  Old: 2 ...` with no separator, so a
  loose type pattern swallows the next record whole and invents a type like
  "Recon Request DeCompress: Old: 2 New: 2". Type names are words, spaces and
  slashes, never a colon, and pinning that down is what stops it. The truncated
  half is dropped rather than guessed at -- the repaint always carries an intact
  copy elsewhere, which is why the record counts are unchanged by the fix.
"""
import re
from pathlib import Path

ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07")

# `type` deliberately excludes ':' -- see the note on run-together records above.
ITEM = re.compile(
    r"(?P<dir>De)?Compress:\s*Old:\s*(?P<old>\d+)\s+(?:New|Size):\s*(?P<new>\d+)\s*"
    r"%:\s*(?P<pct>[\d.]+)%\s*Type:\s*(?P<type>[A-Za-z0-9/][A-Za-z0-9/ -]*?)"
    r"\s+(?P<src>\d+)\s*->\s*(?P<dst>\d+)")
MAIL = re.compile(r"Outbound mail for\s+(?P<who>.+?)\s+-\s+Node\s+(?P<node>\d+)\s+created")
TOTAL = re.compile(r"(?:Original|Compressed):\s*(\d+)\s+(?:Compressed|Decompressed):\s*(\d+)")
# Screen-painted transcripts run phases straight into following text with no
# separator ("...Data (May take a while)Processing Incoming Data from Node 2"), so
# stop at a parenthetical, the next marker, a run of spaces, or end of line.
PHASE = re.compile(r"[■•]\s*(?P<phase>[A-Z][A-Za-z/ ]{2,45}?)(?=\s*\(|\s*[■•]|\s{2,}|\n|$)")

# Longest item type seen in production is "Recon Request" at 13; leave room for a
# type nobody has met yet without letting a mangled line become a database row.
MAX_TYPE_LENGTH = 40


def clean(raw: bytes) -> str:
    """Transcript encoding varies with dosemu's charset settings: some runs emit
    UTF-8 (■ as e2 96 a0), others raw cp437 (■ as byte fe). Try UTF-8 first."""
    stripped = ANSI.sub(b"", raw)
    try:
        txt = stripped.decode("utf-8")
    except UnicodeDecodeError:
        txt = stripped.decode("cp437", errors="replace")
    # Some runs interleave SO/SI and other C0 controls with the text (e.g. "■ \x0f
    # Checking Daily Maintenance"), which breaks anchors that expect the marker to be
    # followed by whitespace. Drop C0 except CR/LF/TAB.
    txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", txt)
    return txt.replace("\r", "\n")


def parse(raw: bytes):
    """Return (records, phases). Records are de-duplicated; phases are in order."""
    text = clean(raw)
    records, phase, seen = [], None, set()
    for line in text.split("\n"):
        for pm in PHASE.finditer(line):
            phase = pm.group("phase").strip()
        for m in ITEM.finditer(line):
            item_type = " ".join(m.group("type").split())
            if not item_type or len(item_type) > MAX_TYPE_LENGTH:
                continue
            rec = dict(kind="item", phase=phase,
                       direction="in" if m.group("dir") else "out",
                       type=item_type,
                       src=int(m.group("src")), dst=int(m.group("dst")),
                       size_before=int(m.group("old")), size_after=int(m.group("new")))
            key = tuple(sorted(rec.items(), key=lambda kv: kv[0]))
            if key not in seen:
                seen.add(key); records.append(rec)
        for m in MAIL.finditer(line):
            rec = dict(kind="mail", phase=phase, to_node=int(m.group("node")),
                       to_name=" ".join(m.group("who").split()))
            key = tuple(sorted(rec.items(), key=lambda kv: kv[0]))
            if key not in seen:
                seen.add(key); records.append(rec)
    phases = []
    for line in text.split("\n"):
        for pm in PHASE.finditer(line):
            p = pm.group("phase").strip()
            if not phases or phases[-1] != p:
                phases.append(p)
    return records, phases


class Transcript:
    """A parsed dosemu transcript.

    Deliberately no size accessor: transcript length scales with how many peers a
    league has, so it is not a health signal. `completed` is.
    """

    def __init__(self, raw: bytes):
        self.raw = raw
        self.text = clean(raw)
        self.records, self.phases = parse(raw)

    @classmethod
    def from_file(cls, path):
        return cls(Path(path).read_bytes())

    @classmethod
    def from_text(cls, text: str):
        """The hub stores transcripts as text; the parser works in bytes."""
        return cls((text or "").encode("utf-8", errors="replace"))

    @property
    def items(self):
        return [r for r in self.records if r["kind"] == "item"]

    @property
    def mails(self):
        return [r for r in self.records if r["kind"] == "mail"]

    @property
    def completed(self) -> bool:
        """Did the game finish, as opposed to dosemu merely coming back?"""
        return "Maintenance Complete" in self.text

    @property
    def booted(self) -> bool:
        """Did dosemu get as far as running DOS at all?"""
        return "Welcome to dosemu2" in self.text or "FDPP kernel" in self.text

    def moved(self, direction=None):
        """The set of (direction, type, src, dst) tuples that crossed the wire.

        This is the stable assertion surface -- compressed sizes drift by a byte
        between otherwise identical runs, and packet bytes are random.
        """
        return {
            (r["direction"], r["type"], r["src"], r["dst"])
            for r in self.items
            if direction is None or r["direction"] == direction
        }

    def types(self, direction=None):
        return {r["type"] for r in self.items
                if direction is None or r["direction"] == direction}

    def ingested_from(self, src: int) -> bool:
        """True if the game actually decompressed something sent by node `src`.

        This is the R-3 detector at transcript level: a run with a bad inbound
        path completes every phase and prints no DeCompress line at all.
        """
        return any(r["direction"] == "in" and r["src"] == src for r in self.items)

    def __repr__(self):
        return (f"<Transcript completed={self.completed} "
                f"phases={len(self.phases)} items={len(self.items)}>")
