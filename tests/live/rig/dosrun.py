#!/usr/bin/env python3
"""Run dosemu2 headlessly on a properly-sized pty and capture the transcript.

Replaces the `script -c ...` wrapper. Three things it fixes:
  * the pty gets an explicit 80x25 window size (DOS needs 25 lines, and a pty
    inherited from a non-tty context has no size at all)
  * TERM is pinned (dosemu refuses to start under TERM=dumb)
  * the exit code returned is dosemu's own, not script(1)'s

Importable as `run(...)`; also usable as a CLI for poking at things by hand.
"""
import argparse, errno, fcntl, os, pty, select, signal, struct, sys, termios, time
from pathlib import Path

TIMEOUT_EXIT = 124


def run(cmd, log, timeout=300.0, term="linux", rows=25, cols=80, cwd=None):
    """Run `cmd` under a pty, writing the raw transcript to `log`.

    Returns dosemu's own exit code, or TIMEOUT_EXIT if it had to be killed.
    """
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    env = dict(os.environ)
    env["TERM"] = term
    env["LINES"] = str(rows)
    env["COLUMNS"] = str(cols)

    pid = os.fork()
    if pid == 0:                                    # child
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        for fd in (0, 1, 2):
            os.dup2(slave, fd)
        if slave > 2:
            os.close(slave)
        os.close(master)
        if cwd:
            os.chdir(cwd)
        try:
            os.execvpe(cmd[0], list(cmd), env)
        finally:
            os._exit(127)

    os.close(slave)
    deadline = time.time() + timeout
    timed_out = False
    # Read until the pty reports EOF (all writers gone), NOT until the first
    # child exits: dosemu's initial process can exit while a child keeps
    # writing, and breaking on waitpid truncates the transcript.
    with open(log, "wb") as fh:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                break
            try:
                r, _, _ = select.select([master], [], [], min(remaining, 1.0))
            except InterruptedError:
                continue
            if master not in r:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError as e:
                if e.errno == errno.EIO:      # slave side closed: normal EOF
                    break
                raise
            if not chunk:
                break
            fh.write(chunk)
            fh.flush()

    status = 0
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        status = 0
    os.close(master)

    if timed_out:
        return TIMEOUT_EXIT
    return os.waitstatus_to_exitcode(status) if status else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--term", default="linux")
    ap.add_argument("--rows", type=int, default=25)
    ap.add_argument("--cols", type=int, default=80)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    if not a.cmd:
        sys.exit("no command given")

    rc = run(a.cmd, a.log, timeout=a.timeout, term=a.term,
             rows=a.rows, cols=a.cols, cwd=a.cwd)
    if rc == TIMEOUT_EXIT:
        print("TIMEOUT", file=sys.stderr)
    sys.exit(rc)


if __name__ == "__main__":
    main()
