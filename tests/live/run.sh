#!/usr/bin/env bash
# Entry point for the live rig. Runs on novatest-hl, as the hub user.
#
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --build'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --setup'
#
# --build provisions all six installs and captures pristine fixtures. It is a
# once-per-machine step; every run after that restores from those fixtures.
#
# --setup installs what the rig interpreter needs (tests/live/requirements.txt).
# Without it a missing dependency surfaces as an ImportError from inside a walk,
# which reads like a broken test rather than a half-built machine, so every run
# checks for them first and says plainly what to do.
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
args=()
for arg in "$@"; do
    case "$arg" in
        --build) build=1 ;;
        --setup) setup=1 ;;
        *) args+=("$arg") ;;
    esac
done

if (( setup )); then
    # Only the rig's own additions. The base environment is deliberately left
    # alone -- see the note in tests/live/requirements.txt about the aiohttp pin.
    "$PYTHON" -m pip install -r "$LIVE_DIR/requirements.txt"
fi

# pyte is what dosdrive reconstructs the screen with, and bre_agent is what
# player.py asks for decisions. Neither is in nova-hub's own requirements: they
# belong to the rig, and the rig venv is built once per machine, so a rebuilt or
# fresh host is exactly where they go missing.
missing=$("$PYTHON" - <<'EOF'
import importlib.util
print(" ".join(m for m in ("pyte", "bre_agent")
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
if (( ! build )) && ! "$PYTHON" - <<'EOF'
import sys
sys.path.insert(0, "/srv/novatest/nova-hub/tests/live")
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

# pytest is run from /tmp: the repo is read-only to the node users and pytest
# wants somewhere to write its cache.
cd /tmp
exec env NOVATEST_RIG=1 "$PYTHON" -m pytest "$REPO/tests/live" \
    -p no:cacheprovider -W ignore::DeprecationWarning "${args[@]:-}"
