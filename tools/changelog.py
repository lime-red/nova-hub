#!/usr/bin/env python3
"""Changelog fragments: each change brings its own file, a release gathers them.

Every PR used to add its lines under "## [Unreleased]" in CHANGELOG.md, so any
two open PRs collided there and whichever merged second had a conflict to
resolve. Now a PR adds one file to changelog.d/ instead, named for its issue
(``changelog.d/127-scores-ans.md``), holding the lines it would have added:

    ### Fixed
    - **A league's Scores no longer show `SCORES.ANS`** (#127). ...

Two PRs never touch the same file, so they never conflict. At release time:

    tools/changelog.py check                 # every fragment is well formed
    tools/changelog.py preview               # the next release's notes
    tools/changelog.py release 0.14.0        # write them into CHANGELOG.md
                                             # and delete the fragments

``release`` puts a "## [0.14.0] - <today>" section under "## [Unreleased]",
with each heading's entries gathered from every fragment in the usual order
(Added, Changed, Deprecated, Removed, Fixed, Security). tools/release.sh then
reads that section from CHANGELOG.md as it always has.
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = ROOT / "changelog.d"
CHANGELOG = ROOT / "CHANGELOG.md"
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
UNRELEASED = "## [Unreleased]"


class FragmentError(ValueError):
    pass


def fragment_paths(directory: Path = FRAGMENTS) -> list[Path]:
    """The fragments, in name order. README.md is the directory's own notes."""
    return sorted(p for p in directory.glob("*.md") if p.name.lower() != "readme.md")


def parse(text: str, name: str = "fragment") -> dict[str, str]:
    """A fragment's entries by heading, each heading's text kept as written."""
    sections: dict[str, list[str]] = {}
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        heading = re.match(r"^###\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            if current not in HEADINGS:
                raise FragmentError(
                    f"{name}:{number}: '### {current}' is not one of {', '.join(HEADINGS)}")
            sections.setdefault(current, [])
        elif line.startswith("#"):
            raise FragmentError(f"{name}:{number}: only '### <heading>' lines may start with #")
        elif current is None:
            if line.strip():
                raise FragmentError(f"{name}:{number}: text before the first '### <heading>'")
        else:
            sections[current].append(line)
    entries = {h: "\n".join(lines).strip("\n") for h, lines in sections.items()}
    entries = {h: body for h, body in entries.items() if body}
    if not entries:
        raise FragmentError(f"{name}: no entries")
    return entries


def gather(paths: list[Path]) -> str:
    """The notes the fragments make together, one section per heading."""
    by_heading: dict[str, list[str]] = {h: [] for h in HEADINGS}
    for path in paths:
        for heading, body in parse(path.read_text(), path.name).items():
            by_heading[heading].append(body)
    parts = []
    for heading in HEADINGS:
        if by_heading[heading]:
            parts.append(f"### {heading}\n" + "\n".join(by_heading[heading]))
    return "\n\n".join(parts)


def release(changelog: str, version: str, notes: str, date: str) -> str:
    """changelog with a "## [version] - date" section of notes under Unreleased."""
    if f"## [{version}]" in changelog:
        raise FragmentError(f"CHANGELOG.md already has a [{version}] section")
    at = changelog.find(UNRELEASED)
    if at < 0:
        raise FragmentError(f"no '{UNRELEASED}' heading in CHANGELOG.md")
    after = changelog.find("\n## ", at + len(UNRELEASED))
    head = changelog[:at] + UNRELEASED + "\n\n"
    rest = changelog[after + 1:] if after >= 0 else ""
    if changelog[at + len(UNRELEASED):after if after >= 0 else None].strip():
        raise FragmentError(
            "CHANGELOG.md has entries under Unreleased: move them into changelog.d/ first")
    section = f"## [{version}] - {date}\n\n{notes}\n"
    return head + section + ("\n" + rest if rest else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check every fragment is well formed")
    sub.add_parser("preview", help="print the next release's notes")
    rel = sub.add_parser("release", help="write the notes into CHANGELOG.md, delete the fragments")
    rel.add_argument("version", help="e.g. 0.14.0 (no leading v)")
    rel.add_argument("--date", default=datetime.date.today().isoformat())
    args = parser.parse_args(argv)

    paths = fragment_paths()
    try:
        if args.command == "check":
            for path in paths:
                parse(path.read_text(), path.name)
            print(f"{len(paths)} fragment(s) ok")
        elif args.command == "preview":
            print(gather(paths) if paths else "(no fragments)")
        else:
            if not paths:
                raise FragmentError("no fragments in changelog.d/: nothing to release")
            CHANGELOG.write_text(release(CHANGELOG.read_text(), args.version.lstrip("v"),
                                         gather(paths), args.date))
            for path in paths:
                path.unlink()
            print(f"CHANGELOG.md: [{args.version.lstrip('v')}] from {len(paths)} fragment(s); "
                  f"fragments deleted (commit both)")
    except FragmentError as e:
        print(f"changelog: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
