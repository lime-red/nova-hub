#!/usr/bin/env python3
"""Fill in movement records from transcripts already stored on processing runs.

Movements are recorded as each run finishes, so without this the view is empty
until new runs accumulate. Every run still holding its dosemu_log can be read
retrospectively -- on production that is the retention window, about 30 days.

Read-only with respect to everything that matters: it only ever inserts into
processing_run_items, and skips any run that already has items, so it is safe to
run twice. Nothing is deleted and no transcript is modified.

    tools/backfill_movements.py --config config.toml            # report only
    tools/backfill_movements.py --config config.toml --apply    # write them

Runs whose transcript has already been dropped by retention cannot be recovered;
they are counted and reported rather than treated as a failure.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import toml
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import ProcessingRun, ProcessingRunFile, ProcessingRunItem
from backend.services.transcript_service import parse


def league_for(db, run):
    """The league to file this run's items under, or None if it cannot be known.

    A run's own league_id is null on production -- every one of 14,276 of them --
    because a batch covers whatever leagues had traffic. The files the run
    generated do carry a league, though, so a run that produced files for exactly
    one league can be attributed with confidence.

    Anything ambiguous stays null rather than guessing. An item filed under the
    wrong league is worse than one filed under none: the first misleads a reader
    who filters, the second merely fails to help.
    """
    if run.league_id is not None:
        return run.league_id

    leagues = {
        league_id
        for (league_id,) in db.query(ProcessingRunFile.league_id)
        .filter(ProcessingRunFile.processing_run_id == run.id,
                ProcessingRunFile.league_id.isnot(None))
        .distinct()
    }
    return leagues.pop() if len(leagues) == 1 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--apply", action="store_true", help="write the records (default: report only)")
    ap.add_argument("--limit", type=int, default=None, help="only consider the newest N runs")
    args = ap.parse_args()

    config = toml.load(args.config)
    db_path = config.get("database", {}).get("path", "./data/nova-hub.db")
    engine = create_engine(f"sqlite:///{db_path}")
    db = sessionmaker(bind=engine)()

    # A run already carrying items was recorded live; leave it alone.
    already = {
        run_id for (run_id,) in db.query(ProcessingRunItem.processing_run_id).distinct()
    }

    q = db.query(ProcessingRun).order_by(ProcessingRun.id.desc())
    if args.limit:
        q = q.limit(args.limit)
    runs = q.all()

    stats = Counter()
    types = Counter()
    pending = []

    for run in runs:
        if run.id in already:
            stats["already recorded"] += 1
            continue
        if not run.dosemu_log:
            # Retention has dropped it, or the run failed before dosemu spoke.
            stats["no transcript left"] += 1
            continue
        try:
            records, _ = parse(run.dosemu_log.encode("utf-8", errors="replace"))
        except Exception as exc:
            print(f"  run {run.id}: could not read transcript ({exc})")
            stats["unreadable"] += 1
            continue

        items = [r for r in records if r.get("kind") == "item"]
        if not items:
            stats["nothing moved"] += 1
            continue

        stats["runs with movements"] += 1
        stats["movements"] += len(items)
        for r in items:
            types[(r["direction"], r["type"])] += 1
            pending.append((run, r))

    print(f"\nexamined {len(runs)} run(s):")
    for label in ("runs with movements", "movements", "nothing moved",
                  "no transcript left", "already recorded", "unreadable"):
        if stats[label]:
            print(f"  {label:22} {stats[label]}")

    if types:
        print("\nwhat would be recorded:")
        for (direction, item_type), n in types.most_common():
            print(f"  {direction:3} {item_type:<18} {n}")

    if not args.apply:
        print("\n(report only -- pass --apply to write these records)")
        return

    # A run covering several leagues concatenated their transcripts before
    # storing them, so its items cannot be split between them after the fact.
    # league_for() attributes what it can and leaves the rest null. Live
    # recording does better because it sees each league's output separately.
    attributed = 0
    for run, record in pending:
        league_id = league_for(db, run)
        attributed += 1 if league_id is not None else 0
        db.add(ProcessingRunItem(
            processing_run_id=run.id,
            league_id=league_id,
            occurred_at=run.started_at,
            direction=record["direction"],
            item_type=record["type"],
            src_node=record["src"],
            dst_node=record["dst"],
            size_before=record.get("size_before"),
            size_after=record.get("size_after"),
            phase=record.get("phase"),
        ))
    db.commit()
    print(f"\nwrote {len(pending)} movement record(s), "
          f"{attributed} attributed to a league "
          f"({len(pending) - attributed} could not be and show under 'all leagues')")


if __name__ == "__main__":
    main()
