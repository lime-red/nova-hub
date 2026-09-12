#!/usr/bin/env python3
"""Apply `[processing] retention_days` once, from the command line.

The hub runs this daily on its own once deployed. This script exists for the
*first* pass, which is the one that matters: production has eight months of
never-pruned data behind a setting that said thirty days, so the first run has
far more to do than any later one and deserves to be looked at before it runs.

    ./purge_retention.py --dry-run          # what would go, and how much
    ./purge_retention.py                    # do it
    ./purge_retention.py --vacuum           # do it, then reclaim the file
    ./purge_retention.py --days 60          # override the configured window

Take a backup first. `sqlite3 <db> ".backup <copy>"` is online-safe and is the
same mechanism used before the migration deploy.

--vacuum needs free disk equal to the finished database and holds an exclusive
lock while it runs, so it is opt-in and never happens automatically.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.core.config import init_config
from backend.core.database import get_session, init_database
from backend.services.retention_service import RetentionService


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def _report(result: dict, verb: str) -> None:
    if not result.get("enabled"):
        print("retention is disabled (retention_days <= 0); nothing to do")
        return
    print(f"cutoff: {result['cutoff'].isoformat()} "
          f"({result['retention_days']} days)")
    for label, tier in result["tiers"].items():
        print(f"  {label:<32} {tier['rows']:>8} rows  {_mb(tier['bytes']):>10}")
    print(f"  {'TOTAL':<32} {result['rows']:>8} rows  {_mb(result['bytes']):>10}"
          f"   {verb}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--days", type=int, default=None,
                    help="override [processing] retention_days")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; change nothing")
    ap.add_argument("--vacuum", action="store_true",
                    help="VACUUM afterwards to return the freed pages to the OS")
    a = ap.parse_args()

    config = init_config(a.config)
    init_database(f"sqlite:///{config.database.path}")
    session = get_session()
    try:
        service = RetentionService(
            session,
            a.days if a.days is not None else config.processing.retention_days,
            a.batch_size or config.processing.retention_batch_size,
        )
        if a.dry_run:
            _report(service.preview(), "would be cleared")
            if a.vacuum:
                print("(--vacuum ignored under --dry-run)")
            return 0

        _report(service.purge(), "cleared")
        if a.vacuum:
            print("VACUUM running; this rebuilds the whole file...")
            service.vacuum()
            print("VACUUM complete")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
