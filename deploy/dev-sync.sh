#!/usr/bin/env bash
# Keep the dev box on main: fast-forward the hub and the client to origin/main,
# and rebuild what moved. Run by the nova-dev-sync user timer on novahub-hl
# (deploy/systemd/); safe to run by hand.
#
#   deploy/dev-sync.sh            sync both, as the timer does
#   deploy/dev-sync.sh --dry-run  say what it would do
#
# A tree is only moved when nobody is working in it: on branch main, no
# changes to tracked files, and a fast-forward away. Otherwise it is left alone
# and the reason logged, every run, until someone sorts it out. Work belongs in
# a worktree (.wt/), which this never touches.
#
# Whenever a tree on main is at a commit it has not been built at, it is
# rebuilt: deploy.sh --here for the hub (venv, frontend, migrations, restart),
# the venv synced to its lock for the client. "Built at" is a stamp under
# ~/.local/state/nova-dev-sync, written after a build succeeds, so a checkout
# or pull by hand gets built at the next run too, not only this script's own
# fast-forwards, and a build that failed is tried again.
#
# NOVA_HUB_DIR and NOVA_CLIENT_DIR point elsewhere; the defaults are dev's.
#
# It pulls the tree it lives in, so everything is inside main(): bash has read
# the whole function before git rewrites this file.
set -euo pipefail

main() {
    local dry=0
    [[ "${1:-}" == --dry-run ]] && dry=1
    local hub="${NOVA_HUB_DIR:-$HOME/nova-hub}" client="${NOVA_CLIENT_DIR:-$HOME/nova_client}"
    local uv
    uv=$(command -v uv || echo "$HOME/.local/bin/uv")
    export PATH="$HOME/.local/bin:$PATH"   # pnpm and uv, for deploy.sh

    sync_tree "$hub" "$dry" || true
    sync_tree "$client" "$dry" || true
    if needs_build "$hub" "$dry"; then
        echo "nova-hub: rebuilding with deploy.sh --here"
        (cd "$hub" && sh deploy/deploy.sh --here)
        built "$hub"
    fi
    if needs_build "$client" "$dry"; then
        echo "nova_client: syncing its venv"
        (cd "$client" && "$uv" sync --frozen)
        built "$client"
    fi
}

STATE="${XDG_STATE_HOME:-$HOME/.local/state}/nova-dev-sync"

# Exit 0 when a tree on main is at a commit it was not last built at.
needs_build() {
    local dir="$1" dry="$2" name head
    name=$(basename "$dir")
    [[ -d "$dir/.git" ]] || return 1
    [[ "$(git -C "$dir" symbolic-ref -q --short HEAD || true)" == main ]] || return 1
    head=$(git -C "$dir" rev-parse HEAD)
    [[ "$(cat "$STATE/$name.built" 2>/dev/null || true)" != "$head" ]] || return 1
    if (( dry )); then
        echo "$name: would rebuild at $(git -C "$dir" log -1 --format='%h %s')"
        return 1
    fi
    return 0
}

built() {
    local name
    name=$(basename "$1")
    mkdir -p "$STATE"
    git -C "$1" rev-parse HEAD > "$STATE/$name.built"
}

# Fast-forward a tree that nobody is working in. Exit 0 when it moved.
sync_tree() {
    local dir="$1" dry="$2" name branch behind
    name=$(basename "$dir")
    [[ -d "$dir/.git" ]] || { echo "$name: no git checkout at $dir"; return 1; }
    if ! timeout 120 git -C "$dir" fetch -q origin main; then
        echo "$name: fetch failed; left as it is"
        return 1
    fi
    behind=$(git -C "$dir" rev-list --count HEAD..origin/main)
    (( behind > 0 )) || return 1
    branch=$(git -C "$dir" symbolic-ref -q --short HEAD || echo "(detached)")
    if [[ "$branch" != main ]]; then
        echo "$name: $behind behind origin/main but on $branch; left as it is"
        return 1
    fi
    if ! git -C "$dir" diff --quiet HEAD --; then
        echo "$name: $behind behind origin/main but has local changes; left as it is"
        return 1
    fi
    if ! git -C "$dir" merge-base --is-ancestor HEAD origin/main; then
        echo "$name: main has diverged from origin/main; left as it is"
        return 1
    fi
    if (( dry )); then
        echo "$name: would fast-forward $behind commit(s) to $(git -C "$dir" log -1 --format='%h %s' origin/main)"
        return 1
    fi
    git -C "$dir" merge -q --ff-only origin/main
    echo "$name: fast-forwarded $behind commit(s) to $(git -C "$dir" log -1 --format='%h %s')"
    return 0
}

main "$@"
exit
