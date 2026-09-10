# tests/test_dosemu_runner.py - Unit tests for DosemuRunner failure reporting
#
# These guard R-1: a dosemu run that died was reported as a successful processing
# run, because `script` was invoked without -e and so returned its own exit status
# (always 0) rather than the child's. Each test here fails if that regresses.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.dosemu_runner import DosemuRunner


def _config(tmp_path, **league_overrides):
    league = {
        "game_folder": str(tmp_path / "game"),
        "game_dos_path": "C:\\bbs\\doors\\bre_900",
        "processing_command": "BRE.EXE PLANETARY /DETAILED",
    }
    league.update(league_overrides)
    return {
        "server": {"data_dir": str(tmp_path)},
        "dosemu": {
            "dosemu_path": "/usr/bin/dosemu",
            "timeout": 30,
            "term": "linux",
            "900": {"bre": league},
        },
    }


class _FakeResult:
    def __init__(self, rc):
        self.returncode = rc


@pytest.fixture
def runner(tmp_path):
    return DosemuRunner(_config(tmp_path))


# ---------------------------------------------------------------------------
# script(1) invocation
# ---------------------------------------------------------------------------

class TestScriptInvocation:
    @pytest.mark.asyncio
    async def test_script_is_invoked_with_dash_e(self, runner, tmp_path, monkeypatch):
        """Without -e, script exits 0 even when dosemu failed. This is R-1."""
        captured = {}

        async def fake_exec(*args, **kwargs):
            captured["argv"] = list(args)

            class P:
                returncode = 0

                async def communicate(self):
                    return (b"", None)

            return P()

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_exec
        )
        await runner._run_command(["/usr/bin/dosemu"], tmp_path / "out.log")

        assert captured["argv"][0] == "script"
        assert "-e" in captured["argv"], (
            "script must be invoked with -e or it masks dosemu's exit code"
        )
        # -e has to come before -c's argument, not be swallowed as part of it
        assert captured["argv"].index("-e") < captured["argv"].index("-c")

    @pytest.mark.asyncio
    async def test_existing_transcript_is_not_overwritten_on_failure(
        self, runner, tmp_path, monkeypatch
    ):
        """A failed run's transcript is the only diagnostic; keep it.

        Now that -e makes returncode mean "dosemu failed", the old
        `if returncode != 0 and stdout_data` branch would fire on every real
        failure and clobber the transcript with script's own chatter.
        """
        log = tmp_path / "out.log"
        log.write_bytes(b"the real transcript explaining the failure")

        async def fake_exec(*args, **kwargs):
            class P:
                returncode = 1

                async def communicate(self):
                    return (b"script noise", None)

            return P()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        await runner._run_command(["/usr/bin/dosemu"], log)

        assert log.read_bytes() == b"the real transcript explaining the failure"

    @pytest.mark.asyncio
    async def test_script_stderr_kept_when_there_is_no_transcript(
        self, runner, tmp_path, monkeypatch
    ):
        """Failing before the transcript exists is itself the diagnostic."""
        log = tmp_path / "out.log"

        async def fake_exec(*args, **kwargs):
            class P:
                returncode = 1

                async def communicate(self):
                    return (b"script: cannot open out.log", None)

            return P()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
        await runner._run_command(["/usr/bin/dosemu"], log)

        assert log.read_bytes() == b"script: cannot open out.log"


# ---------------------------------------------------------------------------
# Run status
# ---------------------------------------------------------------------------

class TestRunStatus:
    @pytest.mark.asyncio
    async def test_nonzero_exit_is_an_error(self, tmp_path, monkeypatch):
        runner = DosemuRunner(_config(tmp_path))
        monkeypatch.setattr(
            runner, "_run_command",
            lambda *a, **k: _async(_FakeResult(1)),
        )
        result = await runner.run_game_process("BRE", "900")
        assert result["status"] == "error"
        assert result["returncode"] == 1

    @pytest.mark.asyncio
    async def test_zero_exit_without_marker_configured_is_success(
        self, tmp_path, monkeypatch
    ):
        """No completion_marker set: keep the old exit-code-only behaviour."""
        runner = DosemuRunner(_config(tmp_path))
        monkeypatch.setattr(
            runner, "_run_command", lambda *a, **k: _async(_FakeResult(0))
        )
        monkeypatch.setattr(
            runner, "_parse_dosemu_output", lambda _log: "anything at all"
        )
        result = await runner.run_game_process("BRE", "900")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_zero_exit_missing_marker_is_an_error(self, tmp_path, monkeypatch):
        """dosemu can come back 0 while the game underneath it died part-way."""
        runner = DosemuRunner(
            _config(tmp_path, completion_marker="Planetary Maintenance Complete")
        )
        monkeypatch.setattr(
            runner, "_run_command", lambda *a, **k: _async(_FakeResult(0))
        )
        monkeypatch.setattr(
            runner, "_parse_dosemu_output",
            lambda _log: "Checking Daily Maintenance\nProcessing Incoming Data",
        )
        result = await runner.run_game_process("BRE", "900")
        assert result["status"] == "error"
        assert "never reached" in result["error"]

    @pytest.mark.asyncio
    async def test_zero_exit_with_marker_is_success(self, tmp_path, monkeypatch):
        runner = DosemuRunner(
            _config(tmp_path, completion_marker="Planetary Maintenance Complete")
        )
        monkeypatch.setattr(
            runner, "_run_command", lambda *a, **k: _async(_FakeResult(0))
        )
        monkeypatch.setattr(
            runner, "_parse_dosemu_output",
            lambda _log: "...\nPlanetary Maintenance Complete\n",
        )
        result = await runner.run_game_process("BRE", "900")
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_marker_is_not_applied_to_non_processing_commands(
        self, tmp_path, monkeypatch
    ):
        """BRE SCORES never prints the maintenance marker; don't fail it for that."""
        runner = DosemuRunner(
            _config(
                tmp_path,
                scores_command="BRE.EXE SCORES",
                completion_marker="Planetary Maintenance Complete",
            )
        )
        monkeypatch.setattr(
            runner, "_run_command", lambda *a, **k: _async(_FakeResult(0))
        )
        monkeypatch.setattr(
            runner, "_parse_dosemu_output", lambda _log: "Creating Top Planets by Score"
        )
        result = await runner.run_scores_command("BRE", "900")
        assert result["status"] == "success"


async def _async(value):
    return value


# ---------------------------------------------------------------------------
# dosemu invocation form
# ---------------------------------------------------------------------------

class TestDosemuInvocation:
    """Guards the -K/-E form.

    Handing dosemu a bare host path to the batch file boots DOS, exits 0, and
    runs nothing at all on the July-2026 dosemu2/fdpp packages. Every processing
    run would then be recorded successful having done no work, so this is the one
    thing about the command line worth pinning down.
    """

    @pytest.mark.asyncio
    async def test_batch_is_run_via_dash_k_and_dash_e(self, tmp_path, monkeypatch):
        runner = DosemuRunner(_config(tmp_path))
        seen = {}

        async def capture(cmd, log_file):
            seen["cmd"] = list(cmd)
            return _FakeResult(0)

        monkeypatch.setattr(runner, "_run_command", capture)
        monkeypatch.setattr(runner, "_parse_dosemu_output", lambda _log: "done")
        await runner.run_game_process("BRE", "900")

        cmd = seen["cmd"]
        assert "-K" in cmd and "-E" in cmd, cmd
        assert cmd[cmd.index("-E") + 1] == "PROCESS.BAT", cmd
        # -K takes the directory; the batch must never appear as a bare path arg.
        assert cmd[cmd.index("-K") + 1] == str(
            Path(tmp_path / "dosemu" / "900" / "bre")
        ), cmd
        assert not any(a.endswith("PROCESS.BAT") and "/" in a for a in cmd), cmd
