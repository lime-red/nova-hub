#!/usr/bin/env bash
# Entry point for the live rig. Runs on novatest-hl, as the hub user.
#
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh'
#   ssh novatest-hl 'sudo -u novahub-t /srv/novatest/nova-hub/tests/live/run.sh --build'
#
# --build provisions all six installs and captures pristine fixtures. It is a
# once-per-machine step; every run after that restores from those fixtures.
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
args=()
for arg in "$@"; do
    case "$arg" in
        --build) build=1 ;;
        *) args+=("$arg") ;;
    esac
done

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
