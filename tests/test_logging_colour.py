# tests/test_logging_colour.py - no ANSI in non-terminal output (#84)
#
# Under systemd stdout is the journal, not a terminal. Forced colour put ESC
# bytes in every line, and journald exports such a MESSAGE as an array of byte
# values, which reached OpenObserve as ["27","91",...] instead of text.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.logging_config import configure_logging, get_logger


def test_no_ansi_when_stdout_is_not_a_terminal(capsys):
    configure_logging("INFO")  # binds to pytest's captured, non-tty stdout
    get_logger("processing").warning("Routes command failed")

    out = capsys.readouterr().out
    assert "Routes command failed" in out
    assert "\x1b" not in out
