#!/usr/bin/env python3
"""Drive an interactive DOS program under dosemu2 through a pty.

The screen is reconstructed with pyte, so `screen_text()` returns the real 80x25
character grid rather than a concatenation of redraws. This is the seam a scripted
player -- or later a model -- plugs into.

Modes:
  observe  run the program and dump the screen every --interval seconds, so you can
           discover what an unfamiliar program prompts for
  script   run a sequence of expect/send steps from a JSON file

Notes that matter for dosemu specifically:
  * the pty is given an explicit 80x25 winsize (DOS needs 25 lines; a pty inherited
    from a non-tty context has no size at all)
  * TERM is pinned (dosemu refuses to start under TERM=dumb)
  * output is read until the pty reports EOF, not until the first child exits
"""
import argparse, errno, fcntl, json, os, pty, re, select, signal, struct, sys, termios, time

import pyte

_ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07")
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class _Screen(pyte.Screen):
    """pyte trips over some private CSI sequences dosemu emits (notably a private
    DECSTBM, `set_margins(..., private=True)`). Swallow the extra kwarg rather than
    losing the whole stream."""

    def set_margins(self, *args, **kwargs):
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class DosSession:
    def __init__(self, cmd, cwd=None, term="linux", rows=25, cols=80, transcript=None):
        self.rows, self.cols = rows, cols
        self.screen = _Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.transcript = open(transcript, "wb") if transcript else None
        self.closed = False
        self._buf = bytearray()
        self._wedged = False

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = dict(os.environ)
        env.update(TERM=term, LINES=str(rows), COLUMNS=str(cols))

        pid = os.fork()
        if pid == 0:
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
                os.execvpe(cmd[0], cmd, env)
            finally:
                os._exit(127)

        os.close(slave)
        self.pid, self.master = pid, master

    def pump(self, timeout=0.5):
        """Read whatever is available and feed it to the terminal emulator."""
        deadline = time.time() + timeout
        got = False
        while not self.closed:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                r, _, _ = select.select([self.master], [], [], remaining)
            except InterruptedError:
                continue
            if self.master not in r:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError as e:
                if e.errno == errno.EIO:      # pty EOF: all writers gone
                    self.closed = True
                    break
                raise
            if not chunk:
                self.closed = True
                break
            self._buf += chunk
            try:
                self.stream.feed(chunk)
            except Exception:
                # A parse error leaves pyte's parser generator dead, so every later
                # feed silently does nothing and the screen freezes -- which looks
                # exactly like "the program stopped responding". Rebuild instead.
                self._wedged = True
            if self._wedged:
                self._rebuild()
            if self.transcript:
                self.transcript.write(chunk)
                self.transcript.flush()
            got = True
        return got

    def _rebuild(self):
        """Re-render the whole byte history onto a fresh emulator."""
        self.screen = _Screen(self.cols, self.rows)
        self.stream = pyte.ByteStream(self.screen)
        try:
            self.stream.feed(bytes(self._buf))
            self._wedged = False
        except Exception:
            self._wedged = True

    def screen_text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def raw_text(self, tail=20000):
        """ANSI-stripped view of the recent byte stream.

        The emulated screen is the right thing to *read*, but pyte can wedge on a
        sequence it does not understand, and then the screen silently stops updating.
        Prompt matching therefore also consults the raw stream, which cannot wedge.
        """
        chunk = bytes(self._buf[-tail:])
        try:
            txt = _ANSI.sub(b"", chunk).decode("utf-8", errors="replace")
        except Exception:
            txt = _ANSI.sub(b"", chunk).decode("cp437", errors="replace")
        return _C0.sub("", txt).replace("\r", "\n")

    def send(self, keys):
        os.write(self.master, keys.encode("latin-1"))

    def mash(self, keys, until_gone, max_presses=60, gap=0.25):
        """Send `keys` repeatedly until `until_gone` leaves the screen (or we exit).

        dosemu consumes a lone ESC as the start of an escape sequence, so a single
        press is usually swallowed; repeated presses get one through. This is the
        documented human workaround, mechanised.
        """
        rx = re.compile(until_gone, re.I)
        for i in range(1, max_presses + 1):
            self.send(keys)
            time.sleep(gap)
            self.pump(0.15)
            # Screen only: raw_text is append-only history, so a marker that has
            # scrolled away would still match and the mash would never terminate.
            if self.closed or not rx.search(self.screen_text()):
                return i
        return None

    def until(self, goal, answers, timeout=120.0):
        """Pump until `goal` appears, answering any prompt in `answers` on the way.

        Interactive DOS programs branch on their own data, so the prompts before a
        given screen are not a fixed sequence. Rather than script an exact order,
        state the destination and how to answer whatever shows up.

        Answers are repeatable -- pagination prompts ("Paused", "Continue? (Y/n)")
        recur many times in one run, so firing each only once wedges the walk. To
        stop a prompt that never clears from being hammered, an answer re-fires only
        once the screen has actually changed since it was last used.
        """
        rx_goal = re.compile(goal, re.I)
        pending = [(re.compile(p, re.I), k) for p, k in answers]
        last_fired = {}
        deadline = time.time() + timeout
        while time.time() < deadline and not self.closed:
            self.pump(0.4)
            screen = self.screen_text()
            if rx_goal.search(screen):
                return True
            view = screen + "\n" + self.raw_text(6000)
            for i, (rx, keys) in enumerate(pending):
                if rx.search(view) and last_fired.get(i) != screen:
                    # Answers come from JSON, so they carry the same <CR>/<ESC>
                    # tokens the ordered steps use and need the same expansion.
                    self.send(expand(keys))
                    last_fired[i] = screen
                    time.sleep(0.6)
                    break
        return False

    def wait_for(self, pattern, timeout=30.0):
        """Pump until `pattern` (regex) appears on screen. Returns the match or None."""
        rx = re.compile(pattern, re.I)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.pump(0.4)
            m = rx.search(self.screen_text()) or rx.search(self.raw_text())
            if m:
                return m
            if self.closed:
                break
        return None

    def close(self, grace=3.0):
        deadline = time.time() + grace
        while not self.closed and time.time() < deadline:
            self.pump(0.3)
        try:
            os.killpg(os.getpgid(self.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            _, status = os.waitpid(self.pid, 0)
        except ChildProcessError:
            status = 0
        os.close(self.master)
        if self.transcript:
            self.transcript.close()
        return os.waitstatus_to_exitcode(status) if status else 0


KEYMAP = {"<CR>": "\r", "<LF>": "\n", "<ESC>": "\x1b", "<SP>": " ", "<TAB>": "\t"}


def expand(keys):
    for k, v in KEYMAP.items():
        keys = keys.replace(k, v)
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["observe", "script"], default="observe")
    ap.add_argument("--steps", help="JSON file: [{expect, send, timeout}, ...]")
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--transcript")
    ap.add_argument("--final-wait", type=float, default=120.0,
                    help="after the last step, pump until the program exits")
    ap.add_argument("--cwd")
    ap.add_argument("--term", default="linux")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    if not a.cmd:
        sys.exit("no command given")

    s = DosSession(a.cmd, cwd=a.cwd, term=a.term, transcript=a.transcript)

    if a.mode == "observe":
        end = time.time() + a.duration
        while time.time() < end and not s.closed:
            s.pump(a.interval)
            print("=" * 72)
            print(f"t={time.time()%1000:.1f} closed={s.closed}")
            print(s.screen_text())
            sys.stdout.flush()
        print("=" * 72, "\nFINAL SCREEN:\n" + s.screen_text())
        sys.exit(s.close())

    steps = json.load(open(a.steps))
    for i, st in enumerate(steps, 1):
        pat, keys, to = st.get("expect"), st.get("send"), st.get("timeout", 30)
        gone = st.get("until_gone")
        if st.get("until"):
            answers = [tuple(x) for x in st.get("answers", [])]
            if not s.until(st["until"], answers, to):
                print(f"STEP {i}: never reached {st['until']!r}\n--- screen ---\n{s.screen_text()}")
                s.close()
                sys.exit(4)
            print(f"STEP {i}: reached {st['until']!r}")
            continue
        if pat and st.get("optional"):
            if not s.wait_for(pat, to):
                print(f"STEP {i}: optional {pat!r} not seen, skipping")
                continue
            print(f"STEP {i}: matched optional {pat!r}")
        elif pat:
            if not s.wait_for(pat, to):
                print(f"STEP {i}: TIMEOUT waiting for {pat!r}\n--- screen ---\n{s.screen_text()}")
                s.close()
                sys.exit(2)
            print(f"STEP {i}: matched {pat!r}")
        if keys is not None and gone:
            n = s.mash(expand(keys), gone, st.get("max", 60), st.get("gap", 0.25))
            if n is None:
                print(f"STEP {i}: {keys!r} never cleared {gone!r}\n--- screen ---\n{s.screen_text()}")
                s.close()
                sys.exit(3)
            print(f"STEP {i}: mashed {keys!r} x{n} until {gone!r} gone")
        elif keys is not None:
            s.send(expand(keys))
            print(f"STEP {i}: sent {keys!r}")
    # Pump until the program actually exits. Closing early kills dosemu mid-write,
    # which for a game like BRE means a half-completed RESET and a stale inuse.flg.
    deadline = time.time() + a.final_wait
    while not s.closed and time.time() < deadline:
        s.pump(1.0)
    print(f"--- final screen (exited={s.closed}) ---\n" + s.screen_text())
    rc = s.close()
    if not s.closed:
        print("WARNING: program had not exited; it was killed", file=sys.stderr)
    sys.exit(rc)


if __name__ == "__main__":
    main()
