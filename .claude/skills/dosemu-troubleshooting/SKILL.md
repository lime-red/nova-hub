---
name: dosemu-troubleshooting
description: Diagnose dosemu2/fdpp/comcom64 failures when running DOS programs headlessly from a script, service, or CI job. Use when a dosemu run produces a suspiciously small log, captures no program output, silently skips a batch file, reports a misleading exit code, or when installing/upgrading the dosemu2 stack from the Launchpad PPA.
---

# dosemu2 headless troubleshooting

Symptom-first notes for running DOS programs under **dosemu2 + fdpp + comcom64**
without a human at a terminal — from cron, systemd, a test harness or CI.

Almost every failure here looks identical from the outside: dosemu exits 0 and leaves
a small log. The sections below tell the cases apart.

---

## Rule 0: the exit code is not a success signal

A dosemu run wrapped in `script(1)` reports **`script`'s** exit status, not dosemu's.
`script` exits 0 even when the command it ran failed. Judge a run by its transcript:

| Log size | Meaning |
|---|---|
| ~330–500 bytes | dosemu never started — see *TERM=dumb* |
| ~1.5–2.4 KB | dosemu booted and exited; **no program output captured** |
| several KB and up | healthy run of a real DOS application |

A healthy transcript contains the **FDPP kernel banner** (`FDPP kernel 1.x … Written
by @stsp`). Grep for that plus a size floor.

**Do not hard-code an absolute "healthy" size.** Transcript size scales with how much
the program actually does, and a threshold calibrated on one workload will reject a
legitimate smaller one. The same DOS application produced 19–28 KB on a large data set
and 3.5–5 KB on a small one — both healthy. Assert on the program's own completion
marker; use size only to catch the near-empty cases above.

To get the real exit code, drive the pty yourself instead of shelling out to `script`
(see *Driving dosemu from a program* below).

**If you must keep `script`**, it does record the truth — in its own trailer:

```
Script done on … [COMMAND_EXIT_CODE="1"]
```

Parse that, or use `script -e`, which makes `script` return the child's exit status
instead of 0.

---

## Rule 1: beware false positives from the `script` header

`script` writes the command it ran into the first line of the transcript:

```
Script started on … [COMMAND="/usr/bin/dosemu -f x.conf -E \"ECHO MARK-A\"" …]
```

So `grep MARK-A transcript.log` matches **the header**, not program output. This will
convince you a run worked when nothing executed at all.

Always check *where* the match is (`grep -aob`), or skip the header
(`tail -c +200`), before concluding a marker was really printed.

---

## Symptom: log is ~330 bytes; "terminal lacks the ability to clear the screen"

```
Your terminal lacks the ability to clear the screen or position the cursor
```

**Cause.** dosemu2 refuses to start unless `TERM` names a terminal it can drive. A
process with no controlling terminal gets `TERM=dumb` from `script`, and dosemu exits
1 immediately. This typically appears when a working interactive setup (tmux, a login
shell) is migrated to **systemd**, cron, or CI.

**Fix.** Pin `TERM` explicitly on the subprocess environment; do not inherit it:

```python
env = dict(os.environ)
env["TERM"] = "linux"      # make it configurable
```

---

## Symptom: `<not executed on terminal>` in the script header

**Not an error, and not a failure signal.** `script` writes it whenever *its own*
stdout is not a tty — including the entirely normal cases of redirecting to a file or
`/dev/null`, or being captured by a parent process. It appears in healthy and broken
transcripts alike. Use the FDPP banner and log size instead.

---

## Symptom: DOS runs, but the transcript captures none of its output

The transcript ends around `Process 0 starting: E:\command.com …`. Exit code 0, log
~1.5–2 KB. Nothing your batch file or program printed appears anywhere.

### Step 1: prove whether DOS actually ran

Do **not** assume it didn't. Have DOS write a file rather than print:

```
@ECHO OFF
ECHO ranok > C:\TMP\PROOF.TXT
EXIT
```

Then look for `~/.dosemu/drive_c/tmp/PROOF.TXT` on the host. If it appears, DOS is
executing correctly and this is purely an output-capture problem. This one probe
separates the two failure classes and is worth doing first every time.

### Step 2: understand the two output modes

dosemu2 has two quite different ways of getting DOS output to your terminal, and the
difference is the single biggest source of false diagnoses here:

| Setting | Short-lived program (`ECHO` then `EXIT`) | Long-running full-screen program |
|---|---|---|
| `$_video = "vga"` (default) | **nothing captured** | captured correctly |
| `$_video = "none"` (dumb terminal) | captured | full-screen display **not** rendered |

In **vga mode** the emulated screen is repainted to the terminal *on a timer*
(`$_term_update_freq`). A program that prints one line and exits immediately is gone
before the first repaint, leaving a transcript that looks exactly like "dosemu is
broken" or "the batch never ran".

**So: never smoke-test dosemu with a bare `ECHO` batch.** It gives a false negative in
the default configuration and will send you chasing dosemu versions, package
dependencies and config keys that were never the problem. Probe with the filesystem
side effect above, or with the real long-running program.

Pick the mode deliberately:

- capturing a real DOS application's screen (the usual case) → keep `$_video = "vga"`
- capturing plain stdout from batch files / short commands → `$_video = "none"`

### Step 3: check for dosemu1 leftovers

`$_vga` and `$_graphics` are **not dosemu2 settings** — they are dosemu1 leftovers and
are silently ignored. A config carrying `$_vga = "off"` / `$_graphics = "off"` is not
selecting anything; the effective setting is the default `$_video = "vga"`.

That is usually harmless (vga is what you wanted for a full-screen app), but the keys
are misleading to read, so replace them with a real `$_video` line. Do not conclude
they are the cause of missing output without running Step 1 first.

---

## Symptom: DOS program exits with errorlevel 1 and prints nothing

Specific to **BRE / Falcon's Eye** (and other Solar Realms-family door games), but the
shape generalises to any DOS game with a lock file.

These games implement a mutex with a semaphore file, **`inuse.flg`**, in the game
directory. If a previous run was killed uncleanly — a timeout, a `pkill`, a crashed
dosemu — the file is left behind and every subsequent run refuses to start, exiting
errorlevel 1 with no visible explanation.

The file says so itself:

```
 1.3401914204E+04
If this file exists, then this game thinks that it is running somewhere.
(On another node)  If not, please delete this file!
```

**Fix.** `rm -f inuse.flg` in the game directory before the run.

**For a harness:** clear `inuse.flg` before every run, and treat its presence *after* a
run as a failure signal — a clean exit removes it. Capture `%ERRORLEVEL%` in the batch
(`ECHO RC-WAS-%ERRORLEVEL%`) so the DOS-level exit code reaches the transcript at all;
it is otherwise invisible from the host side.

## Symptom: "Note that DOS needs 25 lines. You might want to enlarge your window"

**Cause.** The pty has no size, or fewer than 25 rows. A pty inherited from a non-tty
context (`ssh -T`, a systemd unit, CI, or `subprocess` with piped stdio) has size 0.

**Fix.** Either set `$_fixed_term_size = "80x25"` in the dosemu config, or set the
window size on the pty you create:

```python
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 80, 0, 0))
```

This is independent of both `TERM` and `$_video` — all three have to be right.

---

## Symptom: a batch file silently does nothing, but inline commands work

`comcom64` (the 64-bit `command.com` dosemu2 uses) **refuses to execute a batch file
at all if it contains a syntax error**, rather than running line by line until the
error the way MS/FreeDOS `command.com` does. The result is total silence, which reads
exactly like "file not found".

**Fix.** Bisect the batch by halving it until it runs. Write batch files with **CRLF**
line endings, and treat undefined `%VAR%` expansions as the first suspect.

Also check DOS can *see* the file: a host path is meaningless to DOS unless it falls
under a mapped drive. Use `-K <hostdir> -E <command>` to set the working directory for
the command, or `-d <hostdir>` to mount a directory as a drive.

---

## Symptom: `ERROR: KVM: error opening /dev/kvm: No such file or directory`

**Red herring.** It appears in healthy and failing runs alike; dosemu falls back to
its own CPU emulation. Not the reason your run failed.

---

## Symptom: `script` hangs, and `ps` shows it in state `T`

Running `ssh -tt host 'script -c …'` (or any `script` invocation in a background
process group with a controlling terminal) gets **stopped by SIGTTOU/SIGTTIN** the
moment it touches the terminal. The command appears to hang and times out with an
empty log.

**Fix.** Don't combine `ssh -tt` with `script`. Drive the pty from your program
instead.

### Driving dosemu from a program

Doing it yourself fixes the pty size, the `TERM` value and the exit code in one place:

```python
master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 80, 0, 0))
pid = os.fork()
if pid == 0:
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    for fd in (0, 1, 2):
        os.dup2(slave, fd)
    os.execvpe(cmd[0], cmd, env)          # env["TERM"] = "linux"
os.close(slave)
# read master until EOF, then waitpid for dosemu's REAL exit code
```

**Read until the pty reports EOF (`OSError` with `errno.EIO`), not until the first
child exits.** dosemu's initial process can exit while a child continues writing;
breaking on `waitpid` truncates the transcript and loses the program's output.

---

## Installing / upgrading the stack from the Launchpad PPA

**Symptom: pinned `.deb` URLs return 404.** The PPA rotates old builds out, so
deployment scripts that hardcode file URLs break over time. Resolve the current
version from the API instead:

```bash
curl -fsSL "https://api.launchpad.net/1.0/~dosemu2/+archive/ubuntu/ppa?ws.op=getPublishedBinaries&binary_name=dosemu2&status=Published"
```

**Symptom: dependency chase that never converges.** Installing individual `.deb`s
leads to `Depends: libdjdev64-0 … none of the choices are installable`, then
`libdjstub64-0`, and so on — the package set has been split and renamed across
versions, and hand-picking debs cannot keep up. `apt` refuses the whole transaction
atomically, so nothing breaks, but nothing installs either.

**Fix.** Add the PPA as a real apt source and let apt resolve the graph. Pin it so it
can only ever supply the dosemu stack:

```
Package: *
Pin: release o=LP-PPA-dosemu2
Pin-Priority: 1

Package: dosemu2 fdpp comcom64 dj64 dj64-* djdev64 djdev64-* libdjdev64-* libdjstub64-* install-freedos
Pin: release o=LP-PPA-dosemu2
Pin-Priority: 990
```

**Symptom: `Signing key … is not bound … SHA1 is not considered secure`.** On Debian
trixie (and other distros with a modern `sqv`), the PPA's signing key is rejected
because of a SHA1 self-signature.

**Fix.** Use `[trusted=yes]` over HTTPS in the sources entry. That is the same trust
model as downloading the `.deb`s directly from Launchpad over HTTPS, which is what the
hand-rolled approach was already doing:

```
deb [trusted=yes] https://ppa.launchpadcontent.net/dosemu2/ppa/ubuntu noble main
```

Keep the whole set (`dosemu2`, `fdpp`, `comcom64`, `dj64`, `libdjdev64-*`,
`install-freedos`) on matching build dates — they are released together.

---

## Symptom: C: is nearly empty / DOS utilities missing

`~/.dosemu/drive_c` contains little more than `tmp/`.

**Cause.** `install-freedos` is not installed, or its userspace step never ran.
dosemu2 boots fine without it — fdpp supplies the kernel and comcom64 the shell — so
this only surfaces when something expects a populated C:.

**Fix.**

```bash
/usr/libexec/dosemu/dosemu-installfreedosuserspace
```

It downloads FreeDOS packages from ibiblio, so it needs network access. A populated
`drive_c` has `appinfo bin doc help links net nls sound apps`.

---

## Symptom: keys reach the DOS program, but ESC never does

Driving an interactive DOS program through a pty, ordinary characters work (`Y` answers
a prompt) and dosemu's own special keys work, but **a bare `\x1b` is swallowed** — an
"[ESC] to Save & Quit" screen never exits.

**Cause.** In terminal mode dosemu treats a lone ESC byte as the beginning of an
escape sequence, so it is consumed rather than delivered to DOS.

**Sending dosemu's special keys** uses the prefix character `$_escchar`, default
**30 = Ctrl-^ = `\x1e`**. That mechanism works reliably:

| Key | Bytes |
|---|---|
| PgDn | `\x1eK3` |
| Home | `\x1eK7` |
| Insert | `\x1eK0` |
| Delete | `\x1eKd` |
| F1 … F10 | `\x1e1` … `\x1e0` |
| F11, F12 | `\x1e-`, `\x1e=` |
| Sticky Shift/Alt/Ctrl | `\x1eS` / `\x1eA` / `\x1eC` |
| literal `^@` | `\x1e\x1e` |

Send `\x1eh` to make dosemu print its own key help to the screen — the authoritative
list for the build you have.

**For ESC specifically there is no documented prefix code**, and these have all been
tried without success: `\x1b`, `\x1b\x1b` (one write and two writes with a gap),
`\x1b` followed by a 15-second wait, `\x1b `, `\x1b\x00`, `\x1e\x1b`, `\x1e[`,
`\x1eKe`, `\x1eK.`, `\x1b[`, `\x1bO`, and setting `$_escchar = (0)` to disable the
prefix entirely.

`Ctrl-C` (`\x03`) does terminate the program, but as an **abort** — for a program whose
ESC means "save and quit", that loses the work.

If you need ESC, budget for it: it is not a one-liner, and check whether the program
offers another way out (a function key, paging to the end) before assuming ESC is
required.

---

## Symptom: transcript text doesn't match the encoding you expected

Parsing a transcript, box-drawing and marker characters come out as mojibake, or a
regex that should match a visible line finds nothing.

**Cause.** The transcript encoding depends on dosemu's charset settings
(`$_external_char_set` / `$_internal_char_set`). Some setups emit **UTF-8** (`■` as
`e2 96 a0`), others pass **cp437** straight through (`■` as byte `fe`). Two logs from
the same application on different hosts can differ.

**Fix.** Try UTF-8 first and fall back:

```python
try:
    txt = stripped.decode("utf-8")
except UnicodeDecodeError:
    txt = stripped.decode("cp437", errors="replace")
```

Anchor regexes on **plain-ASCII** substrings wherever you can — match `Would you like`
rather than a phrase containing `─`, `■` or a non-ASCII dash.

---

## Symptom: screen-painted output runs lines together

In vga mode the transcript is a record of *screen paints*, not a line-oriented log. Two
consequences when parsing:

1. **The same line appears many times.** BRE repaints regions repeatedly, so naive
   counting overcounts badly. De-duplicate on the semantic key (for BRE: phase,
   direction, type, source, destination, sizes) rather than counting line matches.
2. **Adjacent content runs together with no separator**, e.g.
   `…Processing Incoming Data (May take a while)Processing Incoming Data from Node 2`.
   A regex that expects a phrase to end at a newline will silently miss it. End
   patterns on a parenthetical, the next marker glyph, a run of two or more spaces, or
   end-of-line — not on `\n` alone.

Alternatively replay the transcript through a terminal emulator (`pyte`) and read the
reconstructed screen, which removes the repaint duplication at the cost of losing
anything that scrolled past.

---

## Symptom: an expected prompt sometimes appears and sometimes doesn't

Interactive DOS programs branch on their own data and configuration, so a prompt seen
on one run can vanish on the next. In BRE, `RESET` asks "Would you like this to be a
league-wide reset?" only on the node whose address matches the `HOST` entry — and only
*after* the configuration editor has been dismissed, not before the reset confirmation
where you would expect it.

That combination is what makes this so confusing: the prompt is both **conditional**
and **not in the order the flow implies**.

**Fix: drive toward a goal, not through a script.** Instead of an ordered
expect/send list, state the destination screen and how to answer whatever prompts turn
up on the way:

```python
sess.until(goal=r"Configuration Editor",
           answers=[(r"reset the Game", "Y")])
```

This survives every ordering and every conditional branch, because it never asserts
what comes next — only where it is going.

Three things this needs to get right:

- **Answers must be repeatable.** Pagination prompts (`─»>Paused<«─`,
  `Continue? (Y/n)`, `[MORE]`) recur many times in one walk. If each answer fires only
  once, the walk wedges at the second page. Re-fire them — but only once the screen has
  actually changed since that answer was last used, so a prompt that never clears is
  not hammered forever.
- **Anchor goals on text unique to the target screen.** Help and hint pages quote the
  program's own menu names. A goal of `Game Menu` or `(Q)uit` matched inside BRE's
  "Helpful Hints" page and reported success while still sitting at a name prompt.
  `Choice>` — the actual prompt — was unambiguous.
- **Match against the reconstructed screen, not only the raw byte history.** Raw
  history is append-only, so a marker that has scrolled away still matches. For
  "wait until X is *gone*", screen-only is mandatory.

---

## Symptom: a DOS program silently ignores files at a configured absolute path

The program runs perfectly, exits 0, prints all its progress markers, logs no error —
and behaves as though the directory it was told to read is empty. Nothing in the
transcript, the exit code, or the application's own error log says otherwise.

**Cause: the path traverses a directory whose name is not valid DOS 8.3.** A 16-bit
program (Turbo Pascal, most DOS-era compilers) calls `FindFirst` on the literal path.
dosemu happily serves long names to `DIR` and to `CD`, so the path *looks* fine when
you check it by hand from a DOS prompt — but the application's own path handling
cannot walk it and simply matches nothing.

The trap is that the program still works in every other respect, because it reaches
its **own** files by relative path. `dosemu -K <dir>` makes that directory current, so
relative access always succeeds no matter how long the name is. Only the absolute
configured path fails.

```
C:\BBS\DOORS\BRE_900_N02\INBOUND    # BRE_900_N02 is 11 chars -- silently finds nothing
C:\BBS\DOORS\B900N02\INBOUND        # 8 chars -- works
```

**Fix.** Keep every directory component in a configured absolute path to 8 characters
or fewer (plus an optional 3-character extension). Rename the directory; do not try to
make the program cope.

**How to confirm it in one step.** Point the setting at a deliberately short path
(`C:\IN2`), put the same files there, and re-run. If they are suddenly picked up, the
name length was the cause. This is much faster than auditing the config, because it
changes exactly one variable.

**Detecting it in a harness.** There is no error to grep for. The only observables are
the *effects*: the input files are still present afterwards (a healthy run consumes
them), and the transcript contains none of the per-item lines the program emits when it
actually processes something. Assert on those, never on the exit code or log size.

---

## Symptom: dosemu hangs under `faketime` / `libfaketime`

dosemu2 boots normally, the DOS program starts and prints its opening banner, and then
everything stops. No prompt, no error, no further output. The transcript is truncated
at a couple of KB and your runner reports a timeout.

**Cause: `libfaketime` is not async-signal-safe, and dosemu2 reads the clock from a
timer signal handler.** The interposed `clock_gettime` takes a lock; the emulator hits
it from inside a signal handler on the first tick and deadlocks against itself.

Two things confirm the diagnosis quickly:

- **`faketime -f '+0d'` wedges identically.** If a zero shift hangs, the faked *value*
  is irrelevant and the preload itself is the problem.
- `FAKETIME_DONT_FAKE_MONOTONIC=1` does not help.

**Fix: fake the date inside DOS instead of under it.** A TSR that hooks int 21h
AH=2Ah (GET-DATE) gives the DOS program a shifted date while leaving the host clock,
the emulator's timers, and file mtimes alone. `REDATE.COM` is one such:

```
REDATE /iYYYYMMDD     install, return this fixed date
BRE.EXE PLANETARY
REDATE /r             remove
```

An absolute date is also the better primitive: "advance one day" becomes a different
argument rather than offset arithmetic against the host clock.

**Verify it actually took**, and verify the negative case too. Run the same faked day
twice: a program that tracks dates should do its daily work on the first run and
**skip it** on the second. Without that control you cannot tell a working date hook
from a program that does its daily work unconditionally.

---

## Symptom: `DATE /T` or `%DATE%` produces nothing under comcom64

`ECHO %DATE% %TIME%` expands to empty strings, and `DATE /T` is rejected with
`Invalid date` followed by `Batch file aborted - <path>, line N`.

comcom64 does not implement the `/T` switch or the `DATE`/`TIME` pseudo-variables that
later DOS and NT shells provide. Note also that this *aborts the batch* at that line —
so a date echo added for debugging will stop everything after it from running.

**Fix.** Don't try to read the date through the shell. Use an observable the DOS
application itself produces — a dated output file, a log line, a "daily maintenance"
marker. That is the date that actually matters anyway: what the *program* believes,
not what the shell reports.
