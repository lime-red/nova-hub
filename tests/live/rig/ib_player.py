"""A baron at the keyboard: IB -local in a pty, answering what it is asked.

The league scenarios run the game headless (-maint, -league-check). What they
cannot do is be a player in the middle of something while another process
changes the world. A Player runs -local, IB's play-in-your-own-terminal mode,
which takes the world lock per action exactly as a door session does.

It answers by what is on screen, not by a fixed key script: a turn's stages
vary by build and by what the turn turned up (a lottery, a pause, a message),
and a script that loses step plays the wrong menu without saying so. Whatever
it does not recognise, it stops on and reports with the screen.
"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Prompts a baron answers the same way wherever they come up.
ANSWERS = [
    ("Paused", "\r"),
    ("Enter for English", "\r"),
    ("lottery? (Y/n)", "n"),
    ("Goldie Luck's Bank", "0"),
    ("Biological Attack", "0"),
    ("Gooie Kablooie Ops", "0"),
    ("Send a message?", "n"),
]
WELCOME = "(1) Create Realm"
ENTRY = "(1) Play"
# The buy menu, the first stage that waits on the player: a turn is under way.
BUY = re.compile(r"gold and (\d+) turns?\.\s*>")
CONTINUE = "Continue to your next turn?"


class Player:
    """One -local session on a board's data directory."""

    def __init__(self, binary: Path, data: Path, name: str, clock: str = None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("IB_")}
        env.update(TERM="xterm", LANG="C.UTF-8", LC_ALL="C.UTF-8")
        if clock:
            env["IB_CLOCK_OFFSET"] = clock
        argv = [str(binary), "-local", "-name", name, "-data", str(data)]
        self.name = name
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 80, 0, 0))
        self.screen = ""  # everything shown, ANSI stripped
        self.new = ""  # what was shown since the last key
        self.status = None

    # -- the terminal ------------------------------------------------------------

    def _read(self, wait: float) -> bool:
        """Take what the game printed within `wait`; False once it has gone."""
        if self.status is not None:
            return False
        ready, _, _ = select.select([self.fd], [], [], wait)
        if not ready:
            return True
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            _, status = os.waitpid(self.pid, 0)
            self.status = os.waitstatus_to_exitcode(status)
            return False
        text = ANSI.sub("", data.decode("utf-8", "replace"))
        self.screen += text
        self.new += text
        return True

    def send(self, keys: str) -> None:
        self.new = ""
        os.write(self.fd, keys.encode())

    def until(self, *targets, timeout: float = 60):
        """Answer the stock prompts until one of `targets` (a string or a compiled
        pattern) is the newest thing on screen. Returns its match (a regex match,
        or the string)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Wait for the game to go quiet: it is then waiting on a key.
            while self._read(0.3) and self.new and not self._quiet():
                pass
            if self.status is not None:
                raise AssertionError(f"{self.name}'s game exited {self.status} waiting for "
                                     f"{targets}:\n{self.tail()}")
            newest = None
            for want, answer in [(t, None) for t in targets] + ANSWERS:
                found = _last(want, self.new)
                if found and (newest is None or found[0] > newest[0]):
                    newest = (found[0], want, answer, found[1])
            if newest is None:
                continue
            _, want, answer, match = newest
            if answer is None:
                return match
            self.send(answer)
        raise AssertionError(f"{self.name} never reached {targets}:\n{self.tail()}")

    def _quiet(self) -> bool:
        ready, _, _ = select.select([self.fd], [], [], 0.3)
        return not ready

    def tail(self, lines: int = 25) -> str:
        return "\n".join(line.rstrip() for line in self.screen.splitlines()[-lines:])

    # -- what a baron does ----------------------------------------------------------

    def create_realm(self, realm: str) -> None:
        """From the Welcome menu to the Entry menu, with a realm of its own."""
        self.until(WELCOME)
        self.send("1")
        self.until("Name your Realm:")
        self.send(realm + "\r")
        self.until("Confirm? (Y/n)")
        self.send("y")
        self.until(ENTRY)

    def play(self) -> int:
        """Entry menu to the first stage of a turn. Returns the turns it shows."""
        self.send("1")
        return int(self.until(BUY).group(1))

    def finish_turn(self) -> None:
        """Buy nothing, attack nothing: to the end of the turn."""
        self.send("0")
        self.until(CONTINUE)

    def next_turn(self) -> int:
        self.send("y")
        return int(self.until(BUY).group(1))

    def stop_playing(self) -> None:
        """Back to the Entry menu, as a caller leaving the turn loop."""
        self.send("n")
        self.until(ENTRY)

    def quit(self, timeout: float = 30) -> int:
        """Leave from the Entry menu; the game's exit status."""
        self.send("0")
        deadline = time.monotonic() + timeout
        while self.status is None and time.monotonic() < deadline:
            if self._read(0.3) and self._quiet():
                found = [(_last(want, self.new), answer) for want, answer in ANSWERS]
                found = [(f[0], a) for f, a in found if f]
                if found:
                    self.send(max(found)[1])
        if self.status is None:
            self.kill()
            raise AssertionError(f"{self.name}'s game did not exit:\n{self.tail()}")
        return self.status

    def kill(self) -> None:
        if self.status is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
                _, status = os.waitpid(self.pid, 0)
                self.status = os.waitstatus_to_exitcode(status)
            except ProcessLookupError:
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def _last(want, text: str):
    """(position, match) of the last occurrence of `want` in text, or None."""
    if isinstance(want, re.Pattern):
        matches = list(want.finditer(text))
        return (matches[-1].start(), matches[-1]) if matches else None
    at = text.rfind(want)
    return (at, want) if at >= 0 else None
