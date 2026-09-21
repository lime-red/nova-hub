#!/usr/bin/env python3
"""Drive BRE as a *player*, not as maintenance.

`node.run()` covers everything the game will do unattended. It explicitly refuses
FULL, because FULL is the player path and headless it blocks forever on "Do you
want ANSI Graphics? (Y/n)". This module is the answer to that refusal: it sits on
`dosdrive.DosSession` and answers the prompts.

Why the rig needs it: an idle league emits packets on days one, two and three and
then goes quiet. Longer runs of traffic -- and any traffic carrying a score change
or a message -- need someone to actually play.

Two halves, the same shape as `node.run()`:

  * the caller API (`visit`, `read_history`) runs as the rig user and shells into
    the node's own unix user;
  * `main()` is what runs over there, driving dosemu on a pty.

The walks were derived by observation against a real v0.988 install, not from the
docs; `PROMPTS` below is a transcript of what the game actually asks.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from rig.dosdrive import DosSession
from rig.layout import DOSEMU_CONF, LOGS, VENV_PYTHON, League, Node

# BRE's main menu. Anchoring on the banner rather than on "Choice>" matters: the
# prompt string alone also appears on every submenu, and inside the game's own
# help text.
MAIN_MENU = re.compile(r"\(2\) See Status")
PAUSED = r">Paused<"

# A local-mode door file: COM port 0. Without it BRE waits on a modem that is not
# there. Seven lines, CRLF, as SETUP.SR documents.
DOORFILE = "{name}\r\n1\r\n1\r\n0\r\n38400\r\n0\r\n-1\r\n{name}\r\n"

# The BBS user name in that door file should be UNIQUE ACROSS THE LEAGUE. When
# duplicate checking is on -- it is a league setting, page 2 of the configuration
# editor during RESET, and changeable mid-league -- a name already playing on
# another board is refused outright:
#
#     Duplicate User Found on BBS #2 (Test Node 02), Player A
#     ... you cannot join this game.
#
# It is not a warning and there is no prompt to get past it: the session simply
# never reaches the main menu. The rig leaves the setting at its default and
# derives a unique name per node instead, which is correct either way.


def door_user(node_index: int) -> str:
    return f"TEST PILOT {node_index:02d}"


def _prompts(realm: str, on_continue="N"):
    """(regex, keys) pairs, matched in order against the *live prompt only*.

    Order is significant twice over:
      * "Do you wish to continue?" must precede the generic "Continue?", whose
        pattern also matches it -- and that prompt is how BRE advances to the
        next turn, so answering it wrongly either stops after one turn or plays
        every turn the realm has. `on_continue` may be a callable so the count
        can live in the answer.
      * the generic yes/no fallback must come last, or it eats the specific ones.

    `<CR>` on a yes/no prompt takes whichever answer the game capitalised, which
    is what "play a turn without doing anything" means -- the defaults are the
    do-nothing path, and BRE offers them everywhere.
    """
    return [
        # ── getting in, and creating the realm on first play ──────────────
        (r"Do you want ANSI Graphics",   "Y"),
        (r"IBM Characters",              "Y"),
        (r"Name your Realm",             f"{realm}\r"),
        (r"Name Your Empire",            "Y"),
        (r"Would you like Instructions", "N"),
        # ── the daily turn sequence ───────────────────────────────────────
        # Y plays another turn, N ends play and returns to the main menu.
        (r"Do you wish to continue",     on_continue),
        (r"Do you wish to send a message", "\r"),
        (r"Do you wish to visit the Bank", "\r"),
        # "How much will you give? (40; 40)" / "Buy how many Jets? (0; 120)"
        (r"\(\s*[\d,]+;\s*[\d,]+\s*\)\s*$", "\r"),
        # Interplanetary mail is shown at the START OF PLAY, after any local
        # messages -- never under (6) Read Messages -- and each one waits on its
        # own reader prompt, which looks nothing like a menu:
        #     [R]  Reply, [D]  Delete, [I]  Ignore, or [Q]  Quit>
        # Q leaves the mail alone and closes the reader. The text has already
        # been captured by then (see play_turns), so nothing is lost by not
        # paging through the rest.
        (r"\[R\]\s+Reply|\[Q\]\s+Quit>", "Q"),
        (PAUSED,                         "\r"),
        (r"Continue\?",                  "Y"),
        (r"\[Hit a key\]|any key",       "\r"),
        # Quit out of every submenu: Diplomacy, Food Hall, Covert Ops, Bank,
        # Spending, Attacks, Trading.
        (r"Choice>",                     "0"),
        (r"\(y/N\)|\(Y/n\)",             "\r"),
    ]


class Player:
    """One interactive BRE session, as a player."""

    def __init__(self, install: Path, exe: str, dos_path: str, realm: str,
                 user: str = "TEST PILOT", transcript=None, conf=DOSEMU_CONF):
        self.install, self.realm, self.user = Path(install), realm, user
        self.events = []
        self.turn_text = ""

        (self.install / "DOORFILE.SR").write_bytes(
            DOORFILE.format(name=user).encode("latin-1"))
        (self.install / "PLAY.BAT").write_bytes(
            f"@ECHO OFF\r\nC:\r\nCD {dos_path}\r\n{exe} FULL\r\nEXIT\r\n".encode())
        # inuse.flg is the game's mutex; a killed run leaves one behind and every
        # later run exits 1 having printed nothing about why.
        (self.install / "inuse.flg").unlink(missing_ok=True)

        self.session = DosSession(
            ["/usr/bin/dosemu", "-f", str(conf), "-K", str(self.install),
             "-E", "PLAY.BAT"],
            term="linux", transcript=transcript,
        )

    # ── reading the screen ────────────────────────────────────────────────
    @property
    def screen(self) -> str:
        return self.session.screen_text()

    @property
    def prompt(self) -> str:
        """The line the game is waiting on.

        Answers are matched against this and never against dosdrive's
        `raw_text()`: that is append-only history, so a prompt seen once matches
        forever and its answer re-fires into whatever is on screen later. On a
        short provisioning walk that never shows; on a player walk it types
        stray keys into the realm-name field and wanders into the buy menu.
        """
        for line in reversed(self.screen.splitlines()):
            text = line.strip()
            if text and "F2=Extra Information" not in text:
                return text
        return ""

    def at_main_menu(self) -> bool:
        screen = self.screen
        return bool(MAIN_MENU.search(screen)) and "Choice>" in self.prompt \
            and not re.search(PAUSED, screen)

    # ── driving ───────────────────────────────────────────────────────────
    def answer_until(self, done, prompts, timeout=300.0, label="walk"):
        """Answer the live prompt until `done`, or give up.

        An answer re-fires only once the whole screen has changed since it last
        fired. Keying that on the prompt *line* instead stalls forever: every
        page of BRE's instructions ends in the same "Continue? (Y/n)".
        """
        compiled = [(re.compile(p, re.I), k) for p, k in prompts]
        fired, deadline = {}, time.time() + timeout
        while time.time() < deadline and not self.session.closed:
            self.session.pump(0.5)
            screen, prompt = self.screen, self.prompt
            if done(self):
                return True
            for i, (rx, keys) in enumerate(compiled):
                if not rx.search(prompt):
                    continue
                if fired.get(i) == screen:
                    break
                keys = keys() if callable(keys) else keys
                self.events.append(f"{label}: {prompt!r} <- {keys!r}")
                self.session.send(keys)
                fired[i] = screen
                time.sleep(0.5)
                break
        return False

    def start(self, timeout=300.0):
        """Walk from launch to the main menu, creating the realm if new."""
        ok = self.answer_until(lambda p: p.at_main_menu(), _prompts(self.realm),
                               timeout, label="entry")
        if not ok:
            hint = ""
            if "Duplicate User" in self.screen:
                hint = (f" -- the BBS user name {self.user!r} is already playing on "
                        "another board in this league, and BRE refuses the join")
            raise TimeoutError(
                f"never reached BRE's main menu{hint}; last prompt was "
                f"{self.prompt!r}\n--- screen ---\n{self.screen}"
            )
        return self

    def choose(self, key: str, settle: float = 1.5):
        """Press a key at a menu and let the screen catch up."""
        self.session.send(key)
        time.sleep(settle)
        self.session.pump(settle)

    def status(self, timeout=60.0) -> dict:
        """Read (2) See Status and parse it."""
        self.choose("2")
        self.answer_until(lambda p: p.at_main_menu(),
                          [(PAUSED, "\r")], timeout, label="status")
        return parse_status(self.session.raw_text(20000))

    def read_screens(self, *keys, timeout=180.0, label="read") -> str:
        """Press `keys` from the main menu, page through whatever comes back, and
        return the text produced -- then end up back at the main menu.

        Used for the read-only screens (IPScores, Read Messages), where what
        matters is what the game says, not a state change.
        """
        start = len(self.session._buf)
        for key in keys:
            self.choose(key)
        self.answer_until(
            lambda p: p.at_main_menu(),
            [(PAUSED, "\r"), (r"Continue\?", "Y"), (r"\[Hit a key\]|any key", "\r"),
             (r"Choice>", "0")],
            timeout, label=label)
        return _strip(bytes(self.session._buf[start:]))

    # (9) InterPlanetary Ops, (1) View IPScores opens a menu of reports, not a
    # listing: (1) Top Planets by Score ... (5) Top Players by Score ... (0) Quit.
    # Individual realms appear only under the *Players* reports; the Planets ones
    # are per-board totals and never name a realm.
    IP_SCORE_REPORTS = {"planets_by_score": "1", "planets_by_worth": "2",
                        "planets_by_land": "3", "planets_by_density": "4",
                        "players_by_score": "5", "players_by_worth": "6",
                        "players_by_land": "7", "players_by_density": "8"}

    def ip_scores(self, report: str = "players_by_score", timeout=180.0) -> str:
        """(9) InterPlanetary Ops, (1) View IPScores, then one report.

        This is where another planet's realms appear once their packet has been
        ingested, so it is how a scenario checks that a score really crossed.
        """
        key = self.IP_SCORE_REPORTS.get(report, report)
        return self.read_screens("9", "1", key, timeout=timeout, label="ipscores")

    def read_messages(self, timeout=180.0) -> str:
        """(6) Read Messages."""
        return self.read_screens("6", timeout=timeout, label="messages")

    # ── the decision loop ─────────────────────────────────────────────────
    # bre_agent decides *what* to do; only send_message and wait have a real
    # counterpart here yet. Every other action is executed as a do-nothing turn:
    # v0 of the decision module has no notion of how much to buy, and a turn taken
    # on defaults is the honest approximation -- it still moves the score, which
    # is what the rig needs to move between hosts. The decision is logged either
    # way, so when the executor learns to buy things the history already says what
    # it was asked for.
    def agent_round(self, history_path=None, message=None) -> dict:
        """Read the realm's state, ask bre_agent what to do, and do it."""
        from dataclasses import fields

        from bre_agent import State, decide
        from bre_agent import history as agent_history

        status = self.status()
        known = {f.name for f in fields(State)}
        state = State.from_dict({k: v for k, v in status.items() if k in known})
        decision = decide(state)
        if history_path:
            agent_history.record(decision, history_path)

        if decision.action == "send_message":
            self.send_ip_message(message or (
                f"{state.realm_name} reports score {state.score}, "
                f"{state.turns} turns left."))
        elif decision.action != "wait":
            self.play_turns(1)

        self.events.append(f"agent chose {decision.action}")
        return {
            "action": decision.action,
            "goal_weights": decision.goal_weights,
            "scores": decision.scores,
            "excluded": decision.excluded,
            "status": status,
        }

    def play_turns(self, count: int = 1, timeout=900.0):
        """Play `count` turns, taking every default -- the do-nothing path.

        One press of (1) plays a turn and then asks "Do you wish to continue?";
        Y runs straight into the next one. So this is a single trip through Play
        Game, with the answer to that prompt counting down.

        Even a do-nothing turn moves the score, which is the point: a score
        change is the thing that has to survive the trip to another host.
        """
        remaining = {"turns": count}
        start = len(self.session._buf)

        def on_continue():
            remaining["turns"] -= 1
            return "Y" if remaining["turns"] > 0 else "N"

        self.choose("1")
        ok = self.answer_until(lambda p: p.at_main_menu(),
                               _prompts(self.realm, on_continue),
                               timeout, label="turn")
        if not ok:
            raise TimeoutError(
                f"play did not end back at the main menu; last prompt was "
                f"{self.prompt!r}\n--- screen ---\n{self.screen}"
            )
        # Everything the turn printed, kept for callers who need to assert on
        # what the game said rather than on what it stored. Interplanetary mail
        # is only readable here -- BRE shows it at the start of play, after any
        # local messages, and never under (6) Read Messages.
        self.turn_text = _strip(bytes(self.session._buf[start:]))
        played = count - max(remaining["turns"], 0)
        if played < count:
            raise RuntimeError(
                f"only {played} of {count} turns were played -- the realm most "
                f"likely ran out of turns for today"
            )
        return played

    def send_ip_message(self, text: str, timeout=180.0):
        """Send an interplanetary message to every planet.

        (7) Send Messages on the main menu is *local*: it asks "(A-Y,Z=All)
        Send to:" and only reaches realms on this board. Interplanetary mail --
        the kind that becomes a packet -- is (9) InterPlanetary Ops, (7) Send
        Message, and then a scope. (3) All Planets needs no planet number, which
        keeps this independent of how the league is numbered.
        """
        self.choose("9")            # InterPlanetary Operations
        self.choose("7")            # Send Message
        self.choose("3")            # All Planets
        # (?m) inline: dosdrive.wait_for compiles with re.I only, and the editor
        # prompt is never at the start of the screen buffer.
        if not self.session.wait_for(r"(?m)^\s*1>", timeout=30):
            raise RuntimeError(
                f"the message editor never opened; prompt was {self.prompt!r}\n"
                f"--- screen ---\n{self.screen}"
            )
        for line in text.splitlines() or [text]:
            self.session.send(line[:68] + "\r")
            time.sleep(0.4)
        self.session.send("/S\r")   # /S saves, /A aborts, /C clears
        ok = self.answer_until(lambda p: p.at_main_menu(), _prompts(self.realm),
                               timeout, label="message")
        if not ok:
            raise RuntimeError(
                f"did not get back to the main menu after saving the message; "
                f"prompt was {self.prompt!r}\n--- screen ---\n{self.screen}"
            )
        self.events.append(f"sent IP message: {text!r}")

    def quit(self, timeout=180.0):
        """Leave through (0) Quit.

        Quitting properly is not politeness: FULL's outbound half runs on the way
        out -- it is what packs the day's mail into a packet -- and a killed
        session leaves inuse.flg behind, which makes every later run exit 1
        silently.
        """
        self.choose("0")
        deadline = time.time() + timeout
        while not self.session.closed and time.time() < deadline:
            self.session.pump(1.0)
            if re.search(PAUSED, self.screen):
                self.session.send("\r")
        rc = self.session.close(grace=15)
        (self.install / "inuse.flg").unlink(missing_ok=True)
        return rc


# ── parsing ───────────────────────────────────────────────────────────────
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\][^\x07]*\x07")
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip(raw: bytes) -> str:
    """Same treatment transcript_service gives a log: drop ANSI, then C0."""
    text = _ANSI.sub(b"", raw).decode("utf-8", errors="replace")
    return _C0.sub("", text).replace("\r", "\n")


# The See Status block, verbatim from a running v0.988 game:
#     -*Test Realm*-
#     Turns: 8
#     Score: 0
#     Gold: 1000
#     Bank: 251,272           <- absent when zero
#     Population: 100 Million (Tax Rate: 15%)
#     Popular Support: 100%
#     Food: 1000
_FIELDS = {
    "turns": r"Turns:\s*([\d,]+)",
    "score": r"Score:\s*([\d,]+)",
    "gold": r"Gold:\s*([\d,]+)",
    "bank": r"Bank:\s*([\d,]+)",
    "population_millions": r"Population:\s*([\d,]+)\s*Million",
    "tax_rate": r"Tax Rate:\s*([\d,]+)%",
    "popular_support": r"Popular Support:\s*([\d,]+)%",
    "food": r"Food:\s*([\d,]+)",
}


# BRE omits a line entirely rather than printing a zero, so a realm holding no
# cash has no "Gold:" line at all and one with an empty account has no "Bank:".
# Absent therefore means zero for these two, and only these two -- defaulting the
# rest would turn a parse failure into a plausible-looking number.
_ZERO_WHEN_ABSENT = ("gold", "bank")


def parse_status(text: str) -> dict:
    """Pull the status block out of a transcript.

    Reads the *last* occurrence of each field: the screen is repainted, and on a
    VGA terminal an earlier paint of the same block is still in the history.
    """
    out = {}
    for name, pattern in _FIELDS.items():
        found = re.findall(pattern, text, re.I)
        if found:
            out[name] = int(found[-1].replace(",", ""))
        elif name in _ZERO_WHEN_ABSENT:
            out[name] = 0
    realm = re.findall(r"-\*(.+?)\*-", text)
    if realm:
        out["realm_name"] = realm[-1].strip()
    return out


# ── the CLI half: this is what runs as the node's own user ────────────────
def main(argv=None):
    plan = json.loads((argv or sys.argv)[1])
    if plan.get("activity_weight") is not None:
        # The rig's knob: bre_agent scores "be visibly present" against everything
        # else, and turning it up is how a league is made to keep talking without
        # touching any code path.
        from bre_agent import goals
        goals.ACTIVITY_WEIGHT = plan["activity_weight"]
    player = Player(
        install=Path(plan["install"]),
        exe=plan["exe"],
        dos_path=plan["dos_path"],
        realm=plan["realm"],
        user=plan.get("user", "TEST PILOT"),
        transcript=plan.get("transcript"),
    )
    result = {"events": player.events}
    try:
        player.start()
        result["status_before"] = player.status()
        if plan.get("turns"):
            result["turns_played"] = player.play_turns(plan["turns"])
            result["turn_text"] = player.turn_text
        for text in plan.get("ip_messages", []):
            player.send_ip_message(text)
        for _ in range(plan.get("agent_rounds", 0)):
            result.setdefault("decisions", []).append(
                player.agent_round(plan.get("history"), plan.get("message")))
            if player.turn_text:
                result["turn_text"] = player.turn_text
        if plan.get("read_ip_scores"):
            result["ip_scores"] = player.ip_scores(plan["read_ip_scores"])
        if plan.get("read_messages"):
            result["messages"] = player.read_messages()
        result["status_after"] = player.status()
    finally:
        result["events"] = player.events
        try:
            result["rc"] = player.quit()
        except Exception as exc:                     # pragma: no cover
            result["quit_error"] = str(exc)
    print("##PLAYER##" + json.dumps(result))
    return 0


# ── the caller half: this runs as the rig user ────────────────────────────
def _sudo(node: Node, *args) -> list:
    import getpass
    if node.user == getpass.getuser():
        return list(args)
    return ["sudo", "-u", node.user, "--", *args]


def visit(league: League, node: Node, realm: str = None, user: str = None,
          turns: int = 0, ip_messages=(), agent_rounds: int = 0,
          activity_weight=None, history=None, message=None, read_ip_scores=False,
          read_messages: bool = False, tag: str = "play",
          timeout: int = 900) -> dict:
    """Play this node's install as a human would, and report what changed.

    Returns the parsed status before and after, so a scenario can assert on a
    score change rather than merely on a packet appearing.

    `turns` and `ip_messages` say exactly what to do. `agent_rounds` instead hands
    the wheel to bre_agent: each round reads the realm's live state, asks for a
    decision and carries it out. That is the loop the whole three-part design is
    for -- the rig just happens to be its first caller.
    """
    install = league.install_path(node)
    log = LOGS / f"{tag}_{league.dirname(node.index)}_player.log"
    # The decision log is written by the *node's* user, so it cannot live in
    # pytest's tmp_path (owned by the test user, mode 700). LOGS is the rig's
    # shared, world-writable drop for exactly this. Start each session fresh:
    # bre_agent.history appends, and a rerun under the same tag would otherwise
    # read back as two sessions' worth.
    if history is None:
        history = LOGS / f"{tag}_{league.dirname(node.index)}_decisions.jsonl"
    if agent_rounds:
        subprocess.run(_sudo(node, "rm", "-f", str(history)), check=True)
    plan = {
        "install": str(install),
        "exe": league.g.exe,
        "dos_path": league.dos_path(node),
        "realm": realm or f"Realm {node.index:02d}",
        # Unique per node: see door_user().
        "user": user or door_user(node.index),
        "turns": turns,
        "ip_messages": list(ip_messages),
        # True means the default report; a name or key picks another.
        "agent_rounds": agent_rounds,
        "activity_weight": activity_weight,
        "history": str(history) if history else None,
        "message": message,
        "read_ip_scores": ("players_by_score" if read_ip_scores is True
                           else read_ip_scores),
        "read_messages": read_messages,
        "transcript": str(log),
    }
    try:
        result = subprocess.run(
            _sudo(node, str(VENV_PYTHON), "-m", "rig.player", json.dumps(plan)),
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # A killed session leaves the game's mutex behind, and then every later
        # run on this install exits 1 having printed nothing about why -- so one
        # hung walk would fail every test after it. Clear it, and say where the
        # walk got stuck rather than leaving that in a log nobody reads.
        subprocess.run(_sudo(node, "rm", "-f", str(install / "inuse.flg")),
                       check=False)
        tail = subprocess.run(_sudo(node, "tail", "-c", "1500", str(log)),
                              capture_output=True, text=True, check=False)
        raise RuntimeError(
            f"player session on {league.league_id} node {node.index} did not "
            f"finish within {timeout}s -- most likely an unanswered prompt.\n"
            f"--- last of the transcript ---\n{tail.stdout}"
        ) from None
    marker = "##PLAYER##"
    if marker not in result.stdout:
        raise RuntimeError(
            f"player session on {league.league_id} node {node.index} produced no "
            f"result:\nstdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )
    out = json.loads(result.stdout.split(marker, 1)[1].splitlines()[0])
    out["log"] = str(log)
    out["history"] = str(history)
    return out


def read_history(node: Node, path) -> list:
    """Read back a decision log the node user wrote."""
    result = subprocess.run(
        _sudo(node, "cat", str(path)), capture_output=True, text=True, check=True
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


if __name__ == "__main__":
    sys.exit(main())
