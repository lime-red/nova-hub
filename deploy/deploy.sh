#!/bin/sh
# Deploy a tagged version of nova-hub.
#
#   deploy.sh v0.3.1        pin production to that tag
#   deploy.sh               show what is running and what is available
#
# Production sits on a detached HEAD at a tag, never on a branch. A branch moves
# under you; a tag does not, so what is running is whatever someone last chose
# deliberately.
#
# This does everything except restart. git delivers source, not a deployment --
# the frontend has to be rebuilt (dist is not in git) and migrations have to be
# applied. Those are here. The restart stays a separate, human decision.
set -e
LIVE=/home/novahub/nova-hub
DATA=/home/novahub/nova-data
STAMP=$(date +%Y%m%d-%H%M%S)
# How many of this script's own database backups to keep. The newest is the one
# a rollback would use; the rest are there for the case where a fault is noticed
# a deploy or two late.
KEEP_BACKUPS=3
cd "$LIVE"

current_version() {
  .venv/bin/python -c "from backend.core.version import __version__; print(__version__)" 2>/dev/null || echo "unknown"
}

echo "=== running now ==="
git log --oneline -1
echo "version:  $(current_version)"
if git symbolic-ref -q HEAD > /dev/null; then
  echo "pinned:   NO -- on branch $(git rev-parse --abbrev-ref HEAD)"
else
  echo "pinned:   $(git describe --tags --exact-match 2>/dev/null || echo 'detached, not at a tag')"
fi

if [ -z "$1" ]; then
  echo
  echo "=== tags available (fetching) ==="
  timeout 120 git fetch --tags --quiet origin || echo "(fetch failed -- showing what is cached)"
  git tag -l 'v*' | sort -V | tail -10
  echo
  echo "Usage: deploy.sh <tag>"
  exit 0
fi

TARGET=$1

echo
echo "=== guard: the working tree must be clean ==="
# A dirty tree here means someone edited production by hand. Checking out over
# the top would destroy that silently, which is the one thing a deploy script
# must never do.
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

if ! git rev-parse -q --verify "refs/tags/$TARGET" > /dev/null; then
  echo
  echo "STOP: no such tag '$TARGET'. Available:"
  git tag -l 'v*' | sort -V | tail -10
  exit 1
fi

TARGET_COMMIT=$(git rev-list -1 "$TARGET")
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
echo "=== database backup before anything moves ==="
sqlite3 "$DATA/nova-hub.db" ".backup $DATA/nova-hub.db.bak-deploy-$STAMP"
ls -la "$DATA/nova-hub.db.bak-deploy-$STAMP"
echo "$(git rev-parse HEAD)" > /home/novahub/deploy-rollback-commit.txt
echo "rollback commit recorded: $(git rev-parse --short HEAD)"

echo
echo "=== prune old deploy backups (keeping $KEEP_BACKUPS) ==="
# Every deploy leaves a ~70 MB copy behind. Without this the pile grows with no
# ceiling, which is the same shape of problem retention_days was written to fix
# in the database. Only backups this script made are eligible: the bak-deploy-
# prefix is the whole policy, so a backup an operator took by hand under any
# other name is never touched.
ls -1t "$DATA"/nova-hub.db.bak-deploy-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old_backup; do
  echo "removing $(basename "$old_backup") ($(du -h "$old_backup" | cut -f1))"
  rm -f "$old_backup"
done
KEPT=$(ls -1t "$DATA"/nova-hub.db.bak-deploy-* 2>/dev/null | sed 's|.*/|  |')
echo "kept:"
[ -n "$KEPT" ] && echo "$KEPT" || echo "  (none)"

echo
echo "=== check out $TARGET ==="
# Detached on purpose. config.toml and frontend/dist are gitignored, so neither
# is touched by this. Never run `git clean -fdx` here: it would take config.toml,
# frontend/dist and node_modules with it.
git checkout --quiet --detach "$TARGET"
git log --oneline -1
git status --short
echo "(no output above = clean)"

echo
echo "=== rebuild the frontend ==="
# dist is not in git and is what the browser actually loads, so a checkout alone
# leaves the UI on the previous version. pnpm is on PATH for root but not always
# for this shell; call it by the path that exists.
PNPM=$(command -v pnpm || echo /usr/bin/pnpm)
cd "$LIVE/frontend"
"$PNPM" install --frozen-lockfile --prefer-offline 2>&1 | tail -3
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
sqlite3 -readonly "$DATA/nova-hub.db" "pragma integrity_check;" | head -2

echo
echo "=== the new tree loads ==="
.venv/bin/python -c "
import main
from backend.core.version import version_info
print('app version:', main.app.version)
print('build:', version_info())
"

echo
echo "=== tree still clean after building? ==="
git status --short
echo "(no output above = clean)"

echo
echo "=== validate ==="
.venv/bin/python run.py --validate 2>&1 | tail -10

echo
echo "======================================================================"
echo " Deployed $TARGET. The service has NOT been restarted."
echo ""
echo "   sudo systemctl restart nova-hub"
echo ""
echo " Then hard-reload the browser (Ctrl-Shift-R): index.html is not"
echo " content-hashed, so a cached copy keeps asking for the old bundle."
echo ""
echo " To go back:  deploy.sh \$(cat /home/novahub/deploy-rollback-commit.txt)"
echo " is NOT enough -- that is a commit, not a tag. Use the previous tag,"
echo " or restore $DATA/nova-hub.db.bak-deploy-$STAMP if a migration is at"
echo " fault."
echo "======================================================================"
