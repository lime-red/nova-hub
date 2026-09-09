#!/usr/bin/env python3
"""Extract inter-BBS data-item records from a BRE/FE dosemu transcript.

The transcript reveals each item's *type* plus source and destination node -- not its
contents, but enough to assert on what actually moved between nodes:

    DeCompress:  Old:  56  New:1448  %: 96.1%  Type: Recon Update   2-> 1
    Compress: Old: 1448  Size:   36  %: 97.5%  Type: Configupdate   1-> 2
    Outbound mail for SJ Team 1 - Node 2 created.

Caution: in dosemu's vga mode the emulated screen is repainted repeatedly, so the same
line appears several times in one run. Counting raw line matches overcounts badly.
Records are therefore de-duplicated on (phase, direction, type, src, dst, sizes).
"""
import argparse, json, re, sys
from collections import Counter

ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07")

ITEM = re.compile(
    r"(?P<dir>De)?Compress:\s*Old:\s*(?P<old>\d+)\s+(?:New|Size):\s*(?P<new>\d+)\s*"
    r"%:\s*(?P<pct>[\d.]+)%\s*Type:\s*(?P<type>.+?)\s+(?P<src>\d+)\s*->\s*(?P<dst>\d+)")
MAIL = re.compile(r"Outbound mail for\s+(?P<who>.+?)\s+-\s+Node\s+(?P<node>\d+)\s+created")
TOTAL = re.compile(r"(?:Original|Compressed):\s*(\d+)\s+(?:Compressed|Decompressed):\s*(\d+)")
# Screen-painted transcripts run phases straight into following text with no
# separator ("...Data (May take a while)Processing Incoming Data from Node 2"), so
# stop at a parenthetical, the next marker, a run of spaces, or end of line.
PHASE = re.compile(r"[■•]\s*(?P<phase>[A-Z][A-Za-z/ ]{2,45}?)(?=\s*\(|\s*[■•]|\s{2,}|\n|$)")


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
    text = clean(raw)
    records, phase, seen = [], None, set()
    for line in text.split("\n"):
        for pm in PHASE.finditer(line):
            phase = pm.group("phase").strip()
        for m in ITEM.finditer(line):
            rec = dict(kind="item", phase=phase,
                       direction="in" if m.group("dir") else "out",
                       type=" ".join(m.group("type").split()),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--phases", action="store_true")
    a = ap.parse_args()
    records, phases = parse(open(a.log, "rb").read())

    if a.json:
        json.dump({"records": records, "phases": phases}, sys.stdout, indent=2)
        print()
        return
    if a.phases:
        for p in phases:
            print(" ", p)
        return

    items = [r for r in records if r["kind"] == "item"]
    mails = [r for r in records if r["kind"] == "mail"]
    print(f"phases: {len(phases)}   items: {len(items)}   outbound mails: {len(mails)}")
    if items:
        print("\nby type:")
        for (d, t), n in sorted(Counter((r["direction"], r["type"]) for r in items).items()):
            print(f"  {d:3} {t:<18} x{n}")
        print("\nby node pair:")
        for (s, d), n in sorted(Counter((r["src"], r["dst"]) for r in items).items()):
            print(f"  {s} -> {d}  x{n}")
    for m in mails:
        print(f"  mail -> node {m['to_node']} ({m['to_name']})")


if __name__ == "__main__":
    main()
