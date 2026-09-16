#!/usr/bin/env python3
"""Print what moved between nodes in a BRE/FE dosemu transcript.

A thin command-line front end over `backend.services.transcript_service`, which
is where the parsing lives now that the hub reads transcripts too.

    tools/parse_dosemu_log.py run.log
    tools/parse_dosemu_log.py run.log --json
"""
import argparse, json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.transcript_service import parse  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--phases", action="store_true")
    a = ap.parse_args()
    records, phases = parse(Path(a.log).read_bytes())

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
