"""Honour `[processing] retention_days`.

The setting has shipped in `config.toml.example` since the beginning and been
parsed into `ProcessingConfig` the whole time, and nothing has ever read it.
An operator reading that config had every reason to believe old data was pruned
at thirty days. It never was: production reached 478 MB across eight months,
92% of it older than the retention window it thought it had, growing ~700 MB a
year with no ceiling. A setting that lies is worse than no setting.

**Two-tier, not delete.** What is big and what is worth keeping are not the same
rows -- they are different *columns* of the same rows:

| | |
|---|---|
| `processing_runs` | 267 MB, essentially all of it `dosemu_log` |
| `processing_run_files` | 179 MB over 170k rows, essentially all `file_data` |
| `packets` | 8 MB |

So retention drops the bulk text and keeps the row. Run history, counts, exit
codes, per-league attribution and the sequence record all survive; the 18 KB
transcript of a run nobody has looked at since February does not. `file_size`
is left as it was, so the row still says how big the thing was.

No *row* is deleted outright. That is deliberate: every id stays valid, no
foreign key is orphaned, and a purge cannot cascade into data somebody still
needs. It also means the pass is re-runnable and its effect is monotonic.

**The same setting has a filesystem half.** `dosemu_runner` writes every
transcript to `<data_dir>/logs/dosemu/*.log` before `processing_service` reads it
into `ProcessingRun.dosemu_log`, so the text exists twice. Blanking the column
and leaving the file behind honours the setting only halfway, which is how
production accumulated 53,930 log files and 860 MB over eight months while the
database tier was working correctly. Aged log files are therefore deleted -- and
here deletion is right, because the file is the bulk content, there is no row to
preserve, and anything still inside the window keeps both copies.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, text

from backend.logging_config import get_logger
from backend.models.database import ProcessingRun, ProcessingRunFile

logger = get_logger(context="retention")

DEFAULT_BATCH_SIZE = 500

# What gets blanked, in the form (model, column, human name).
TIERS = (
    (ProcessingRun, "dosemu_log", "processing run transcripts"),
    (ProcessingRun, "stdout_log", "processing run stdout"),
    (ProcessingRun, "stderr_log", "processing run stderr"),
    (ProcessingRunFile, "file_data", "generated file contents"),
)

_TIMESTAMP = {
    ProcessingRun: "started_at",
    ProcessingRunFile: "created_at",
}

# The one directory on disk this service will touch, relative to data_dir, and
# the only suffix it will remove from it. Both are deliberately narrow: this is
# the single place in the codebase that deletes operator files, so it should be
# impossible to point somewhere else by accident.
LOG_SUBDIR = ("logs", "dosemu")
LOG_SUFFIX = ".log"


class RetentionService:
    """Drop aged bulk content in bounded batches.

    Construct with a session and the number of days to keep. `retention_days`
    of 0 (or less) disables the pass entirely -- that is the "keep everything"
    setting, and it is honoured rather than ignored.
    """

    def __init__(
        self,
        db,
        retention_days: int,
        batch_size: int = DEFAULT_BATCH_SIZE,
        data_dir: str | Path | None = None,
    ):
        self.db = db
        self.retention_days = retention_days
        self.batch_size = max(1, batch_size)
        # Optional so that every existing caller and test keeps working and
        # simply does no filesystem work.
        self.data_dir = Path(data_dir) if data_dir else None

    @property
    def enabled(self) -> bool:
        return self.retention_days > 0

    @property
    def log_dir(self) -> Path | None:
        """Where dosemu transcripts land, or None if this service has no data_dir."""
        if self.data_dir is None:
            return None
        return self.data_dir.joinpath(*LOG_SUBDIR)

    def cutoff(self, now: datetime | None = None) -> datetime:
        # Rows are stamped with datetime.utcnow(), so compare in the same frame.
        return (now or datetime.utcnow()) - timedelta(days=self.retention_days)

    def preview(self) -> dict:
        """What a purge would do, without doing it.

        Required before the first production run: this deletes, and the point of
        rehearsing is to see the number before trusting it.
        """
        if not self.enabled:
            return {"enabled": False, "tiers": {}, "rows": 0, "bytes": 0}

        cutoff = self.cutoff()
        tiers, total_rows, total_bytes = {}, 0, 0
        for model, column, label in TIERS:
            col = getattr(model, column)
            rows, size = self.db.query(
                func.count(col), func.coalesce(func.sum(func.length(col)), 0)
            ).filter(
                getattr(model, _TIMESTAMP[model]) < cutoff, col.isnot(None)
            ).one()
            tiers[label] = {"rows": rows or 0, "bytes": int(size or 0)}
            total_rows += rows or 0
            total_bytes += int(size or 0)

        files, file_bytes = self._aged_logs(cutoff)
        return {
            "enabled": True,
            "retention_days": self.retention_days,
            "cutoff": cutoff,
            "tiers": tiers,
            "rows": total_rows,
            "bytes": total_bytes,
            "log_files": len(files),
            "log_bytes": file_bytes,
        }

    def purge(self) -> dict:
        """Blank aged bulk columns. Returns the same shape as preview()."""
        if not self.enabled:
            logger.debug("retention disabled (retention_days <= 0); nothing purged")
            return {"enabled": False, "tiers": {}, "rows": 0, "bytes": 0}

        cutoff = self.cutoff()
        tiers, total_rows, total_bytes = {}, 0, 0

        for model, column, label in TIERS:
            rows, freed = self._purge_column(model, column, cutoff)
            tiers[label] = {"rows": rows, "bytes": freed}
            total_rows += rows
            total_bytes += freed

        log_files, log_bytes = self.purge_logs(cutoff)

        if total_rows or log_files:
            logger.info(
                f"retention: cleared {total_rows} aged value(s), "
                f"{total_bytes / 1_048_576:.1f} MB, and deleted {log_files} aged "
                f"log file(s), {log_bytes / 1_048_576:.1f} MB, "
                f"older than {cutoff.isoformat()}"
            )
        return {
            "enabled": True,
            "retention_days": self.retention_days,
            "cutoff": cutoff,
            "tiers": tiers,
            "rows": total_rows,
            "bytes": total_bytes,
            "log_files": log_files,
            "log_bytes": log_bytes,
        }

    def _purge_column(self, model, column: str, cutoff: datetime) -> tuple[int, int]:
        """One column, in batches, committing as it goes.

        Batching matters on a database this size: production has 13,000 aged runs
        and 156,000 aged file rows, and a single statement would hold a write
        lock across all of them while the hub is trying to process packets. Each
        batch is its own transaction, so an interrupted purge leaves a consistent
        database and simply resumes where it stopped next time.
        """
        col = getattr(model, column)
        stamp = getattr(model, _TIMESTAMP[model])
        rows_done, bytes_done = 0, 0

        while True:
            batch = self.db.query(model.id, func.length(col)).filter(
                stamp < cutoff, col.isnot(None)
            ).limit(self.batch_size).all()
            if not batch:
                break

            ids = [row[0] for row in batch]
            bytes_done += sum(int(row[1] or 0) for row in batch)
            self.db.query(model).filter(model.id.in_(ids)).update(
                {col: None}, synchronize_session=False
            )
            self.db.commit()
            rows_done += len(ids)

        return rows_done, bytes_done

    def _aged_logs(self, cutoff: datetime) -> tuple[list[Path], int]:
        """Log files older than the cutoff, and their total size.

        Age comes from the file's own mtime rather than from its name. The names
        do carry a timestamp, but a name is a claim and a stat is a fact, and a
        file whose name will not parse should not therefore become immortal.
        """
        log_dir = self.log_dir
        if log_dir is None or not log_dir.is_dir():
            return [], 0

        # cutoff comes from utcnow(), which is naive -- .timestamp() on a naive
        # datetime reads it as *local* time, which would shift the window by the
        # host's UTC offset. Say the frame explicitly so the comparison against
        # st_mtime (epoch seconds, UTC) is right wherever this runs.
        cutoff_ts = cutoff.replace(tzinfo=timezone.utc).timestamp()
        aged, total = [], 0
        for entry in log_dir.iterdir():
            # No recursion, no symlink following, nothing but plain .log files
            # directly in this directory.
            if entry.suffix != LOG_SUFFIX or entry.is_symlink() or not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff_ts:
                aged.append(entry)
                total += stat.st_size
        return aged, total

    def purge_logs(self, cutoff: datetime | None = None) -> tuple[int, int]:
        """Delete aged dosemu transcripts from disk.

        Failures are counted as skips, not raised: a log file that cannot be
        removed is a permissions problem for an operator, and it must not be able
        to abort a retention pass that has real database work left to do.
        """
        if not self.enabled or self.log_dir is None:
            return 0, 0

        aged, _ = self._aged_logs(cutoff or self.cutoff())
        deleted, freed = 0, 0
        for entry in aged:
            try:
                size = entry.stat().st_size
                entry.unlink()
            except OSError as e:
                logger.warning(f"retention: could not delete {entry.name}: {e}")
                continue
            deleted += 1
            freed += size
        return deleted, freed

    def vacuum(self) -> None:
        """Reclaim the freed pages.

        Separate from purge() and never automatic: VACUUM rebuilds the whole file
        and needs free disk equal to the finished database, and it takes an
        exclusive lock for the duration. That is an operator's decision, not a
        background task's.
        """
        logger.info("retention: VACUUM starting")
        self.db.commit()
        self.db.execute(text("VACUUM"))
        logger.info("retention: VACUUM complete")


def _data_dir(config) -> str | None:
    """`[server] data_dir`, from either a parsed Config or the raw dict."""
    server = getattr(config, "server", None)
    if server is not None:
        return getattr(server, "data_dir", None)
    try:
        return (config or {}).get("server", {}).get("data_dir")
    except AttributeError:
        return None


def from_config(db, config) -> RetentionService:
    """Build a service from a parsed `Config` or the raw dict."""
    data_dir = _data_dir(config)
    processing = getattr(config, "processing", None)
    if processing is not None:
        return RetentionService(
            db, processing.retention_days,
            getattr(processing, "retention_batch_size", DEFAULT_BATCH_SIZE),
            data_dir=data_dir,
        )
    raw = (config or {}).get("processing", {})
    return RetentionService(
        db,
        raw.get("retention_days", 30),
        raw.get("retention_batch_size", DEFAULT_BATCH_SIZE),
        data_dir=data_dir,
    )
