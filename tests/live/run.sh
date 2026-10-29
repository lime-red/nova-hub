#!/usr/bin/env bash
# Entry point for the live rig. Runs on novatest-hl, as the hub user.
#
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --build'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --setup'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --try my-branch -k ib'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --client my-branch -k ib'
#
# --build provisions all six installs and captures pristine fixtures. It is a
# once-per-machine step; every run after that restores from those fixtures.
#
# --setup installs what the rig interpreter needs (tests/live/requirements.txt).
# Without it a missing dependency surfaces as an ImportError from inside a walk,
# which reads like a broken test rather than a half-built machine, so every run
# checks for them first and says plainly what to do.
#
# --try REF runs the suite from a pushed branch, tag or commit instead of this
# checkout, which stays on main. REF is checked out (detached) as a git worktree
# at /srv/novatest/try/<REF>, the host-state files git does not carry are linked
# in from here, and that tree's own run.sh is run with the remaining arguments.
# An experiment has to be pushed to gitea to be tried, which is the point: what
# the rig runs is always something the repo has. The venv is shared, so a REF
# whose requirements differ from this checkout's gets a warning, not its own.
# Remove a finished one with: git -C /srv/novatest/nova-hub worktree remove <dir>
#
# Every run also readies what the Immortal Barons scenarios play: the latest IB
# release and IB trunk (rig.ib_builds, which downloads or builds them), and
# nova-client at --client REF, default main (rig.nova_client). Pin IB with
# IB_RELEASE=<tag> or IB_TRUNK=<commit> in the environment.
#
# Everything here is leagues 900B and 901B on novatest-hl. Nothing in this tree
# touches a production host or a production league number.
set -euo pipefail

LIVE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$LIVE_DIR/../.." && pwd)"
PYTHON="${NOVATEST_PYTHON:-/srv/novatest/venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
    echo "no rig interpreter at $PYTHON (set NOVATEST_PYTHON)" >&2
    exit 1
fi

build=0
setup=0
try_ref=""
client_ref=main
args=()
while (( $# )); do
    case "$1" in
        --build) build=1 ;;
        --setup) setup=1 ;;
        --try)
            [[ $# -ge 2 ]] || { echo "--try needs a branch, tag or commit" >&2; exit 2; }
            try_ref="$2"; shift ;;
        --client)
            [[ $# -ge 2 ]] || { echo "--client needs a branch, tag or commit" >&2; exit 2; }
            client_ref="$2"; shift ;;
        *) args+=("$1") ;;
    esac
    shift
done

TRY_ROOT=/srv/novatest/try
# Not tracked by git, and needed by a tree checked out beside this one.
HOST_STATE=(config.toml frontend/dist dosemu_configs deploy/inventory.yml)

if [[ -n "$try_ref" ]]; then
    if [[ ! -d "$TRY_ROOT" || ! -w "$TRY_ROOT" ]]; then
        echo "$TRY_ROOT is missing or not writable by $(id -un); create it once with:" >&2
        echo "  sudo install -d -o $(id -un) -g $(id -gn) -m 755 $TRY_ROOT" >&2
        exit 1
    fi
    git -C "$REPO" fetch -q origin
    sha=$(git -C "$REPO" rev-parse -q --verify "origin/$try_ref^{commit}" \
          || git -C "$REPO" rev-parse -q --verify "$try_ref^{commit}") \
        || { echo "no branch, tag or commit '$try_ref' on origin" >&2; exit 1; }
    tree="$TRY_ROOT/${try_ref//\//-}"
    if [[ -e "$tree/.git" ]]; then
        # Forced: a try tree is a copy of the repo, never somewhere to edit.
        git -C "$tree" checkout -q --force --detach "$sha"
        git -C "$tree" clean -q -fd
    else
        git -C "$REPO" worktree prune
        git -C "$REPO" worktree add -q --detach "$tree" "$sha"
    fi
    for path in "${HOST_STATE[@]}"; do
        if [[ -e "$REPO/$path" && ! -e "$tree/$path" ]]; then
            ln -s "$REPO/$path" "$tree/$path"
        fi
    done
    for req in requirements.txt tests/live/requirements.txt; do
        if ! cmp -s "$REPO/$req" "$tree/$req"; then
            echo "WARNING: $req differs from $REPO's, and the venv is shared:" >&2
            echo "  the run uses what $PYTHON has, not what $try_ref asks for" >&2
        fi
    done
    echo "trying $try_ref at $(git -C "$tree" log -1 --format='%h %s') in $tree" >&2
    if (( build )); then args=(--build "${args[@]}"); fi
    if (( setup )); then args=(--setup "${args[@]}"); fi
    args=(--client "$client_ref" "${args[@]}")
    exec "$tree/tests/live/run.sh" "${args[@]}"
fi

# The checkout itself is meant to track main, with experiments going through
# --try. Say so when it has fallen behind, rather than test stale code quietly.
# A detached HEAD is a --try tree, which is behind on purpose.
if git -C "$REPO" symbolic-ref -q HEAD >/dev/null && git -C "$REPO" fetch -q origin 2>/dev/null; then
    behind=$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo 0)
    if (( behind > 0 )); then
        echo "WARNING: $REPO is $behind commit(s) behind origin/main" >&2
        echo "  update it with: git -C $REPO pull --ff-only" >&2
    fi
fi

if (( setup )); then
    # nova-hub's own pins plus the rig's additions: see tests/live/requirements.txt
    "$PYTHON" -m pip install -r "$LIVE_DIR/requirements.txt"
fi

# sr_harness drives the games (and brings pyte, its screen emulator), and
# bre_agent is what player.py asks for decisions. The rig venv is built once per
# machine, so a rebuilt or fresh host is exactly where they go missing.
missing=$("$PYTHON" - <<'EOF'
import importlib.util
print(" ".join(m for m in ("sr_harness", "pyte", "bre_agent")
                if importlib.util.find_spec(m) is None))
EOF
)
if [[ -n "$missing" ]]; then
    echo "rig interpreter $PYTHON is missing: $missing" >&2
    echo "install what the rig needs with: $0 --setup" >&2
    echo "  (or: $PYTHON -m pip install -r $LIVE_DIR/requirements.txt)" >&2
    exit 1
fi

# A pristine fixture is only pristine on the day it was captured: the games
# number packets by game day, so yesterday's fixture restores to a game whose
# next packet is .002. Rebuild rather than let every scenario fail on arithmetic.
if (( ! build )) && ! "$PYTHON" - "$LIVE_DIR" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
from rig import fixtures
sys.exit(1 if fixtures.stale() else 0)
EOF
then
    echo "fixtures are not from today - rebuilding (this takes a few minutes)" >&2
    build=1
fi

if (( build )); then
    # Provisioning drives each node as its own unix user, so this needs the rig
    # owner's passwordless sudo. Fixtures are captured from trees that have never
    # processed a packet.
    ( cd "$LIVE_DIR" && "$PYTHON" -m rig.build )
fi

# Immortal Barons: both builds, and the client that syncs the boards. A build
# that cannot be had is recorded, and its scenarios fail saying why, so neither
# step stops the BRE and FE scenarios from running.
( cd "$LIVE_DIR" && "$PYTHON" -m rig.ib_builds ) || echo "WARNING: rig.ib_builds failed; the IB scenarios will say why" >&2
( cd "$LIVE_DIR" && "$PYTHON" -m rig.nova_client "$client_ref" ) || echo "WARNING: rig.nova_client failed; the IB scenarios cannot sync" >&2

# pytest is run from /tmp: the repo is read-only to the node users and pytest
# wants somewhere to write its cache.
cd /tmp
# "${args[@]}", not "${args[@]:-}". On an empty array the :- form expands to one
# EMPTY STRING rather than to nothing, and pytest reads that as a second path to
# collect -- which, from here, is /tmp. It then walked /tmp/pulse-* and
# /tmp/systemd-private-*, set rootdir to /, and died with six PermissionError
# collection errors before running a single test. Passing any argument at all,
# even -q, hid it, so the plain no-argument invocation documented at the top of
# this file was the only one that failed. ${args[@]} on an empty array expands
# to nothing under set -u on bash 4.4+ (the rig is on 5.2).
exec env NOVATEST_RIG=1 "$PYTHON" -m pytest "$REPO/tests/live" \
    -p no:cacheprovider -W ignore::DeprecationWarning "${args[@]}"
