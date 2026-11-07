#!/usr/bin/env bash
# The nightly live-rig run, as the live-rig workflow starts it on novatest-hl.
#
#   sudo -n -u novahub-t /srv/novatest/nova-hub/tests/live/nightly.sh main <sha>
#   sudo -n -u novahub-t /srv/novatest/nova-hub/tests/live/nightly.sh my-branch <sha>
#
# On main, it moves the rig checkout forward to <sha> (fast-forward only) and
# runs the suite from it, with --setup so the venv follows the new uv.lock.
# Anything else (a workflow run dispatched on a branch) runs through --try at
# <sha>, so the checkout stays on main.
#
# This is the one command the runner's user may run as novahub-t (see
# tests/live/runner/). It pulls the file it is read from, so everything below
# is inside main(): bash has parsed the whole function before git rewrites it.
set -euo pipefail

main() {
    local ref="${1:?usage: nightly.sh <ref> <sha>}" sha="${2:?usage: nightly.sh <ref> <sha>}"
    local live repo
    live="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    repo="$(cd "$live/../.." && pwd)"
    # Under the rig lock before anything moves: a run started by hand may be
    # playing from this checkout right now. run.sh, exec'd below, keeps it.
    # shellcheck source=rig_lock.sh
    source "$live/rig_lock.sh"
    rig_lock "$live/nightly.sh" "$@"
    if [[ ! "$sha" =~ ^[0-9a-f]{7,40}$ ]]; then
        echo "not a commit: $sha" >&2
        exit 2
    fi

    git -C "$repo" fetch -q origin
    if [[ "$ref" != main ]]; then
        exec "$repo/tests/live/run.sh" --setup --try "$sha" -q
    fi

    local branch
    branch=$(git -C "$repo" symbolic-ref -q --short HEAD || echo "(detached)")
    if [[ "$branch" != main ]]; then
        echo "STOP: $repo is on $branch, not main; it is meant to track main" >&2
        exit 1
    fi
    if ! git -C "$repo" diff --quiet HEAD --; then
        echo "STOP: $repo has local changes to tracked files:" >&2
        git -C "$repo" status --short --untracked-files=no >&2
        exit 1
    fi
    # The run tests the commit the workflow was started for. Main may have
    # moved on since; that commit gets its own run.
    git -C "$repo" merge -q --ff-only "$sha"
    echo "rig checkout at $(git -C "$repo" log -1 --format='%h %s')" >&2
    exec "$repo/tests/live/run.sh" --setup -q
}

main "$@"
exit
