#!/bin/sh
# Deploy nova-hub. The same script on every host (#126).
#
#   deploy.sh                 show what is running and what is available
#   deploy.sh v0.13.0         pin this hub to a tag (production, the test hub)
#   deploy.sh --ref <ref>     pin to a branch or commit instead (the test hub)
#   deploy.sh --here          build what is checked out, in place, and restart (dev)
#
# Every host lays the hub out the same way under the service user's home:
# ~/nova-hub for the code and ~/nova-data for the data. Set NOVA_HUB_DIR or
# NOVA_HUB_DATA to point elsewhere.
#
# A pinned hub sits on a detached HEAD, never on a branch. A branch moves under
# you; a tag does not, so what is running is whatever someone last chose
# deliberately. Pinning does everything except restart: git delivers source,
# not a deployment, so the venv is synced, the frontend rebuilt (dist is not in
# git) and migrations applied here. The restart stays a separate, human
# decision.
#
# --here is for a working tree someone is editing (dev). It checks nothing out
# and does not care whether the tree is clean. It syncs the venv with the dev
# tools included, rebuilds, migrates, and restarts the hub's user unit if there
# is one.
#
# A pinned deploy is run from a copy outside the tree (~/deploy.sh): checking
# out a new version rewrites deploy/deploy.sh under the shell reading it.
set -e
# The database backup this makes holds every client's secret hash: the
# hub's user only, like the database itself.
umask 077
LIVE=${NOVA_HUB_DIR:-$HOME/nova-hub}
DATA=${NOVA_HUB_DATA:-$HOME/nova-data}
STAMP=$(date +%Y%m%d-%H%M%S)
ROLLBACK_FILE=$HOME/deploy-rollback-commit.txt
# How many of this script's own database backups to keep. The newest is the one
# a rollback would use; the rest are there for the case where a fault is noticed
# a deploy or two late.
KEEP_BACKUPS=3
UV=$(command -v uv || echo "$HOME/.local/bin/uv")
PNPM=$(command -v pnpm || echo /usr/bin/pnpm)

MODE=show
TARGET=
case "$1" in
  "") ;;
  --here) MODE=here ;;
  --ref)
    [ -n "$2" ] || { echo "--ref needs a branch or commit"; exit 2; }
    MODE=ref; TARGET=$2 ;;
  -*) echo "Usage: deploy.sh [<tag> | --ref <branch|commit> | --here]"; exit 2 ;;
  *) MODE=tag; TARGET=$1 ;;
esac

cd "$LIVE"

if [ "$MODE" = tag ] || [ "$MODE" = ref ]; then
  SELF=$(readlink -f "$0")
  case "$SELF" in
    "$(readlink -f "$LIVE")"/*)
      echo "STOP: run a pinned deploy from a copy outside the tree, not $SELF:"
      echo "  cp $LIVE/deploy/deploy.sh ~/deploy.sh && ~/deploy.sh $*"
      exit 1 ;;
  esac
fi

current_version() {
  .venv/bin/python -c "from backend.core.version import __version__; print(__version__)" 2>/dev/null || echo "unknown"
}

# The database is wherever config.toml says, not assumed.
database_path() {
  .venv/bin/python - <<'EOF'
import tomllib
from pathlib import Path
with open("config.toml", "rb") as f:
    path = Path(tomllib.load(f)["database"]["path"])
print(path if path.is_absolute() else Path.cwd() / path)
EOF
}

# sqlite3's own backup and integrity check, through Python: the sqlite3 CLI is
# not on every host.
db_backup() {
  .venv/bin/python -c "
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
" "$1" "$2"
}
db_integrity() {
  .venv/bin/python -c "
import sqlite3, sys
print(sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True).execute('pragma integrity_check').fetchone()[0])
" "$1"
}

# What `uv sync` would remove or downgrade, from its --dry-run listing
# (" - name==old" and " + name==new"). v0.7.0's lock would have quietly
# downgraded cryptography and certifi on production; this is that check.
venv_losses() {
  awk '
    $1 == "-" { split($2, a, "=="); old[a[1]] = a[2] }
    $1 == "+" { split($2, a, "=="); new[a[1]] = a[2] }
    END {
      for (p in old) {
        if (!(p in new)) { print "  removes   " p " " old[p]; continue }
        cmd = "printf \"%s\\n%s\\n\" \"" old[p] "\" \"" new[p] "\" | sort -V | head -1"
        cmd | getline lowest; close(cmd)
        if (lowest == new[p] && new[p] != old[p]) print "  downgrades " p " " old[p] " -> " new[p]
      }
    }'
}

echo "=== running now ==="
git log --oneline -1
echo "version:  $(current_version)"
if git symbolic-ref -q HEAD > /dev/null; then
  echo "pinned:   NO -- on branch $(git rev-parse --abbrev-ref HEAD)"
else
  echo "pinned:   $(git describe --tags --exact-match 2>/dev/null || echo 'detached, not at a tag')"
fi

if [ "$MODE" = show ]; then
  echo
  echo "=== tags available (fetching) ==="
  timeout 120 git fetch --tags --quiet origin || echo "(fetch failed -- showing what is cached)"
  git tag -l 'v*' | sort -V | tail -10
  echo
  echo "Usage: deploy.sh [<tag> | --ref <branch|commit> | --here]"
  exit 0
fi

DB=$(database_path)

if [ "$MODE" != here ]; then
  echo
  echo "=== guard: the working tree must be clean ==="
  # A dirty tree here means someone edited a pinned hub by hand. Checking out
  # over the top would destroy that silently, which is the one thing a deploy
  # script must never do.
  if [ -n "$(git status --porcelain)" ]; then
    echo "STOP: uncommitted changes in $LIVE"
    git status --short
    echo
    echo "Work out where these came from before deploying."
    exit 1
  fi
  echo "clean"

  echo
  echo "=== fetch ==="
  timeout 120 git fetch --tags origin

  if [ "$MODE" = tag ]; then
    if ! git rev-parse -q --verify "refs/tags/$TARGET" > /dev/null; then
      echo
      echo "STOP: no such tag '$TARGET'. Available:"
      git tag -l 'v*' | sort -V | tail -10
      exit 1
    fi
    TARGET_COMMIT=$(git rev-list -1 "$TARGET")
  else
    # A branch is taken as gitea has it, not as a stale local copy.
    TARGET_COMMIT=$(git rev-parse -q --verify "origin/$TARGET^{commit}" 2>/dev/null \
      || git rev-parse -q --verify "$TARGET^{commit}" 2>/dev/null) \
      || { echo "STOP: no branch or commit '$TARGET' on origin"; exit 1; }
  fi

  if [ "$TARGET_COMMIT" = "$(git rev-parse HEAD)" ]; then
    echo
    echo "Already at $TARGET. Nothing to do."
    exit 0
  fi

  echo
  echo "=== what changes ==="
  git diff --stat HEAD "$TARGET_COMMIT" | tail -20
  echo
  echo "migrations this brings in:"
  NEW_MIGRATIONS=$(git diff --name-only --diff-filter=A HEAD "$TARGET_COMMIT" -- alembic/versions/ || true)
  if [ -z "$NEW_MIGRATIONS" ]; then
    echo "  (none)"
  else
    echo "$NEW_MIGRATIONS" | sed 's/^/  /'
  fi

  echo
  echo "=== the venv $TARGET needs (dry run, before anything moves) ==="
  # Judged against the target's lock, read without checking it out.
  PROBE=$(mktemp -d)
  git show "$TARGET_COMMIT:pyproject.toml" > "$PROBE/pyproject.toml"
  git show "$TARGET_COMMIT:uv.lock" > "$PROBE/uv.lock"
  UV_PROJECT_ENVIRONMENT="$LIVE/.venv" "$UV" sync --frozen --no-dev --dry-run \
    --project "$PROBE" > "$PROBE/plan.txt" 2>&1 || { cat "$PROBE/plan.txt"; rm -rf "$PROBE"; exit 1; }
  grep -E '^ [-+~] ' "$PROBE/plan.txt" || echo "  (no change)"
  LOSSES=$(venv_losses < "$PROBE/plan.txt")
  # A venv uv would build afresh (another Python, or none there) lists every
  # package as new and none as lost, so it is caught here instead.
  if grep -qE '^Would (create|replace)' "$PROBE/plan.txt"; then
    LOSSES="$LOSSES
  rebuilds the venv: $(grep -E '^Would (create|replace)' "$PROBE/plan.txt")"
  fi
  rm -rf "$PROBE"
  if [ -n "$LOSSES" ] && [ "$NOVA_HUB_ALLOW_VENV_LOSSES" != 1 ]; then
    echo
    echo "STOP: the venv would lose or go back on these:"
    echo "$LOSSES"
    echo "Nothing has changed. If that is intended, run again with"
    echo "NOVA_HUB_ALLOW_VENV_LOSSES=1."
    exit 1
  fi
fi

echo
echo "=== database backup before anything moves ==="
db_backup "$DB" "$DATA/nova-hub.db.bak-deploy-$STAMP"
ls -la "$DATA/nova-hub.db.bak-deploy-$STAMP"
if [ "$MODE" != here ]; then
  git rev-parse HEAD > "$ROLLBACK_FILE"
  echo "rollback commit recorded: $(git rev-parse --short HEAD)"
fi

echo
echo "=== prune old deploy backups (keeping $KEEP_BACKUPS) ==="
# Every deploy leaves a copy of the database behind. Without this the pile
# grows with no ceiling. Only backups this script made are eligible: the
# bak-deploy- prefix is the whole policy, so a backup an operator took by hand
# under any other name is never touched.
ls -1t "$DATA"/nova-hub.db.bak-deploy-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old_backup; do
  echo "removing $(basename "$old_backup") ($(du -h "$old_backup" | cut -f1))"
  rm -f "$old_backup"
done
KEPT=$(ls -1t "$DATA"/nova-hub.db.bak-deploy-* 2>/dev/null | sed 's|.*/|  |')
echo "kept:"
[ -n "$KEPT" ] && echo "$KEPT" || echo "  (none)"

if [ "$MODE" != here ]; then
  echo
  echo "=== check out $TARGET ==="
  # Detached on purpose. config.toml and frontend/dist are gitignored, so neither
  # is touched by this. Never run `git clean -fdx` here: it would take
  # config.toml, frontend/dist and node_modules with it.
  git checkout --quiet --detach "$TARGET_COMMIT"
  git log --oneline -1
  git status --short
  echo "(no output above = clean)"
fi

echo
echo "=== sync the venv from uv.lock ==="
# Exactly the lock: what CI, the rig and every other hub run. Dev keeps its
# test tools; a pinned hub leaves them out.
if [ "$MODE" = here ]; then
  "$UV" sync --frozen 2>&1 | tail -12
else
  "$UV" sync --frozen --no-dev 2>&1 | tail -12
fi

echo
echo "=== rebuild the frontend ==="
# dist is not in git and is what the browser actually loads, so a checkout alone
# leaves the UI on the previous version. CI=true: pnpm never stops to ask.
cd "$LIVE/frontend"
CI=true "$PNPM" install --frozen-lockfile --prefer-offline 2>&1 | tail -3
rm -rf dist
"$PNPM" run build 2>&1 | tail -3
echo "--- dist: ---"
ls dist
cd "$LIVE"

echo
echo "=== apply migrations ==="
.venv/bin/alembic current 2>&1 | tail -1
.venv/bin/alembic upgrade head 2>&1 | tail -5
echo "now at:"
.venv/bin/alembic current 2>&1 | tail -1
echo "integrity: $(db_integrity "$DB")"

echo
echo "=== the new tree loads ==="
.venv/bin/python -c "
import main
from backend.core.version import version_info
print('app version:', main.app.version)
print('build:', version_info())
"

if [ "$MODE" != here ]; then
  echo
  echo "=== tree still clean after building? ==="
  git status --short
  echo "(no output above = clean)"
fi

echo
echo "=== validate ==="
.venv/bin/python run.py --validate 2>&1 | tail -10

if [ "$MODE" = here ]; then
  echo
  if systemctl --user cat nova-hub.service > /dev/null 2>&1; then
    echo "=== restart (user unit) ==="
    systemctl --user restart nova-hub
    sleep 3
    systemctl --user is-active nova-hub
  else
    echo "No user unit for nova-hub here: restart the hub yourself."
  fi
  exit 0
fi

echo
echo "======================================================================"
echo " Deployed $TARGET. The service has NOT been restarted."
echo ""
echo "   sudo systemctl restart nova-hub"
echo ""
echo " Then hard-reload the browser (Ctrl-Shift-R): index.html is not"
echo " content-hashed, so a cached copy keeps asking for the old bundle."
echo ""
echo " To go back:  ~/deploy.sh <previous tag>, then"
echo " restore $DATA/nova-hub.db.bak-deploy-$STAMP if a migration is at"
echo " fault. The commit it replaced is in $ROLLBACK_FILE."
echo "======================================================================"
