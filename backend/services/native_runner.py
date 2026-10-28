"""Run a game that is a native program on the hub's host, not a DOS door.

The counterpart of dosemu_runner for games such as Immortal Barons: no
emulator, no batch file and no pty transcript, just the binary with its
arguments, its output captured to a log beside dosemu's, and a timeout that
takes the whole process group with it.
"""

import asyncio
import os
import signal
from pathlib import Path

from backend.core.clock import utcnow

# Beside logs/dosemu, under the same retention (retention_service.LOG_SUBDIRS).
LOG_SUBDIR = ("logs", "native")


class NativeRunner:
    def __init__(self, data_dir: str, timeout: int = 300):
        self.data_dir = Path(data_dir)
        self.timeout = timeout

    async def run(self, label: str, argv: list[str], cwd: Path) -> dict:
        """Run argv in cwd and return the dosemu runner's result shape:
        {"status": "success"|"error"|"timeout", "returncode", "output", "log_file", "error"}.

        `label` names the log file, e.g. "555_ib_maint".
        """
        log_dir = self.data_dir.joinpath(*LOG_SUBDIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{label}_{utcnow().strftime('%Y%m%d_%H%M%S')}.log"

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=str(cwd),
                # Its own process group, so a timeout can kill everything it
                # started without reaching the hub.
                start_new_session=True,
            )
        except OSError as e:
            return {"status": "error", "returncode": -1, "error": f"could not start {argv[0]}: {e}",
                    "output": ""}

        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as e:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if isinstance(e, asyncio.CancelledError):
                raise
            return {"status": "timeout", "returncode": process.returncode,
                    "error": f"{argv[0]} timed out after {self.timeout}s", "output": ""}

        output = out.decode("utf-8", errors="replace")
        log_file.write_text(output)
        result = {"returncode": process.returncode, "output": output, "log_file": str(log_file)}
        if process.returncode != 0:
            last = output.strip().splitlines()[-1] if output.strip() else ""
            result.update(status="error",
                          error=f"{Path(argv[0]).name} exited {process.returncode}"
                                + (f": {last}" if last else "") + f" (log: {log_file})")
        else:
            result["status"] = "success"
        return result
