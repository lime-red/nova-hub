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
# When the hub moves, deploy.sh --here rebuilds it in place: venv, frontend,
# migrations, restart. When the client moves, its venv is synced to its lock.
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

    if sync_tree "$hub" "$dry"; then
        echo "hub moved: rebuilding with deploy.sh --here"
        (cd "$hub" && sh deploy/deploy.sh --here)
    fi
    if sync_tree "$client" "$dry"; then
        echo "client moved: syncing its venv"
        (cd "$client" && "$uv" sync --frozen)
    fi
}

# Exit 0 when the tree moved, 1 when it did not (always 1 with --dry-run).
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
