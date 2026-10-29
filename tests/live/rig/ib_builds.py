#!/usr/bin/env python3
"""The Immortal Barons builds the IB scenarios play: the latest release, and trunk.

Both, every run, so a change on trunk that the hub or the client has to answer
shows up the night it lands rather than the day it is released. run.sh calls
this before pytest:

    /srv/novatest/venv/bin/python -m rig.ib_builds
    IB_RELEASE=v0.2.5 IB_TRUNK=e9d34f2 ... -m rig.ib_builds   # pin either one

- **release** is GitHub's latest release, the linux-amd64 archive checked
  against the release's own SHA256SUMS.
- **trunk** is the upstream default branch, built here: a clone in IB_ROOT/src
  and the Go its go.mod names, fetched from go.dev and checked against go.dev's
  own sha256. Go is not installed on the rig any other way.

A build is kept under IB_ROOT/builds by name and never rebuilt, so a run on an
unchanged trunk costs a fetch. What this run is to play goes in current.json,
which the scenarios read: per kind, the ref, the commit, the binary and what
`-version` says, or the error that stopped it. An error is not a skip: every
scenario of that kind fails with it, which is what a nightly run must show when
trunk stops building. If the network is down, the previous current.json entry
is kept, and says so.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rig.layout import IB_CURRENT, IB_KINDS, IB_ROOT, IB_UPSTREAM

BUILDS = IB_ROOT / "builds"
SRC = IB_ROOT / "src"
GO = IB_ROOT / "go"
CACHE = IB_ROOT / "cache"
# Builds no longer in current.json that are kept anyway, newest first, so a
# regression can be bisected against the last few nights without a rebuild.
KEEP = 6


def _get(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "nova-hub-rig"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _git(*args, cwd=SRC) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def _version(binary: Path) -> str:
    out = subprocess.run([str(binary), "-version"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=30)
    return (out.stdout or out.stderr).strip().splitlines()[0]


def _record(kind: str, ref: str, commit: str, binary: Path) -> dict:
    return {"kind": kind, "ref": ref, "commit": commit, "binary": str(binary),
            "version": _version(binary)}


# -- release --------------------------------------------------------------------

def release(tag: str = None) -> dict:
    if not tag:
        latest = json.loads(_get(f"https://api.github.com/repos/{IB_UPSTREAM}/releases/latest"))
        tag = latest["tag_name"]
    binary = BUILDS / f"release-{tag}" / "immortal-barons"
    if not binary.is_file():
        base = f"https://github.com/{IB_UPSTREAM}/releases/download/{tag}"
        asset = f"immortal-barons-{tag}-linux-amd64.tar.gz"
        sums = {}
        for line in _get(f"{base}/SHA256SUMS").decode().splitlines():
            if line.strip():
                digest, name = line.split(maxsplit=1)
                sums[name.strip().lstrip("*")] = digest
        data = _get(f"{base}/{asset}")
        digest = hashlib.sha256(data).hexdigest()
        if sums.get(asset) != digest:
            raise RuntimeError(f"{asset} has sha256 {digest}; SHA256SUMS says {sums.get(asset)}")
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            member = next((m for m in tar.getmembers()
                           if m.isfile() and Path(m.name).name == "immortal-barons"), None)
            if member is None:
                raise RuntimeError(f"{asset} holds no immortal-barons binary")
            payload = tar.extractfile(member).read()
        _install(binary, payload)
    commit = _git("rev-parse", f"{tag}^{{commit}}") if (SRC / ".git").is_dir() else ""
    return _record("release", tag, commit, binary)


# -- trunk ----------------------------------------------------------------------

def _fetch_source() -> None:
    if not (SRC / ".git").is_dir():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "-q", f"https://github.com/{IB_UPSTREAM}.git", str(SRC), cwd=IB_ROOT)
    _git("fetch", "-q", "--tags", "--force", "origin")
    _git("remote", "set-head", "origin", "--auto")


def _go(src: Path) -> Path:
    """The Go that go.mod asks for, from go.dev, checked against go.dev's sha256."""
    version = None
    for line in (src / "go.mod").read_text().splitlines():
        words = line.split()
        if words[:1] == ["toolchain"]:
            version = words[1].removeprefix("go")
            break
        if words[:1] == ["go"] and version is None:
            version = words[1]
    if not version:
        raise RuntimeError("go.mod names no Go version")
    go = GO / version / "go" / "bin" / "go"
    if go.is_file():
        return go
    name = f"go{version}.linux-amd64.tar.gz"
    listing = json.loads(_get("https://go.dev/dl/?mode=json&include=all"))
    want = next((f["sha256"] for release in listing for f in release["files"]
                 if f["filename"] == name), None)
    if want is None:
        raise RuntimeError(f"go.dev lists no {name}")
    data = _get(f"https://go.dev/dl/{name}", timeout=600)
    if hashlib.sha256(data).hexdigest() != want:
        raise RuntimeError(f"{name} does not match go.dev's sha256")
    staging = GO / f".{version}.part"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(staging, filter="tar")
    shutil.rmtree(GO / version, ignore_errors=True)
    staging.rename(GO / version)
    return go


def trunk(ref: str = None) -> dict:
    if not (SRC / ".git").is_dir():
        _fetch_source()
    ref = ref or _git("rev-parse", "--abbrev-ref", "origin/HEAD").removeprefix("origin/")
    commit = _git("rev-parse", f"origin/{ref}^{{commit}}") if _has(f"origin/{ref}") \
        else _git("rev-parse", f"{ref}^{{commit}}")
    binary = BUILDS / f"trunk-{commit[:7]}" / "immortal-barons"
    if not binary.is_file():
        _git("checkout", "-q", "--force", "--detach", commit)
        _git("clean", "-q", "-fdx")
        go = _go(SRC)
        env = dict(os.environ, CGO_ENABLED="0", GOOS="linux", GOARCH="amd64",
                   GOTOOLCHAIN="local", GOFLAGS="-modcacherw",
                   GOCACHE=str(CACHE / "build"), GOMODCACHE=str(CACHE / "mod"),
                   GOPATH=str(CACHE / "gopath"))
        out = binary.with_name("immortal-barons.part")
        out.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([str(go), "build", "-trimpath", "-o", str(out),
                                 "./cmd/immortal-barons"],
                                cwd=SRC, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            shutil.rmtree(binary.parent, ignore_errors=True)
            raise RuntimeError(f"trunk {commit[:7]} does not build:\n{result.stderr.strip()}")
        out.rename(binary)
    return _record("trunk", ref, commit, binary)


def _has(rev: str) -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", rev], cwd=SRC,
                          capture_output=True).returncode == 0


def _install(binary: Path, payload: bytes) -> None:
    binary.parent.mkdir(parents=True, exist_ok=True)
    part = binary.with_name(binary.name + ".part")
    part.write_bytes(payload)
    part.chmod(0o755)
    part.rename(binary)


# -- current.json -----------------------------------------------------------------

def current() -> dict:
    try:
        return json.loads(IB_CURRENT.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _prune(keep: dict) -> None:
    wanted = {Path(entry["binary"]).parent for entry in keep.values() if "binary" in entry}
    others = sorted((d for d in BUILDS.glob("*") if d.is_dir() and d not in wanted),
                    key=lambda d: d.stat().st_mtime, reverse=True)
    for stale in others[KEEP:]:
        shutil.rmtree(stale, ignore_errors=True)


def main() -> int:
    if not IB_ROOT.is_dir() or not os.access(IB_ROOT, os.W_OK):
        print(f"{IB_ROOT} is missing or not writable; create it once with:\n"
              f"  sudo install -d -o $(id -un) -g $(id -gn) -m 755 {IB_ROOT}", file=sys.stderr)
        return 1
    previous = current()
    try:
        # Both kinds want it: trunk is built from it, and it names a release's commit.
        _fetch_source()
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"could not fetch {IB_UPSTREAM}: {e}", file=sys.stderr)
    pins = {"release": os.environ.get("IB_RELEASE"), "trunk": os.environ.get("IB_TRUNK")}
    makers = {"release": release, "trunk": trunk}
    chosen = {}
    for kind in IB_KINDS:
        try:
            chosen[kind] = makers[kind](pins[kind])
        except Exception as e:  # noqa: BLE001 - recorded, and shown by every scenario
            last = previous.get(kind, {})
            # Unreachable (urllib's errors are OSErrors, git's are CalledProcessErrors)
            # is not the same as broken: a RuntimeError is a bad checksum or a
            # trunk that does not build, and that must not be papered over.
            unreachable = isinstance(e, (OSError, subprocess.CalledProcessError))
            if unreachable and "binary" in last and not pins[kind]:
                print(f"IB {kind}: {e}; playing the last one, {last['version']}", file=sys.stderr)
                chosen[kind] = dict(last, stale=f"could not check for a newer one: {e}")
            else:
                chosen[kind] = {"kind": kind, "ref": pins[kind] or "", "error": str(e)}
        chosen[kind]["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    IB_CURRENT.write_text(json.dumps(chosen, indent=2) + "\n")
    _prune(chosen)
    for kind, entry in chosen.items():
        print(f"IB {kind:7} {entry.get('version') or 'NONE: ' + entry['error'].splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
