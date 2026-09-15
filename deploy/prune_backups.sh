#!/bin/sh
# One-off: clear the backup pile that built up during the September work.
#
#   prune_backups.sh           show what would go, delete nothing
#   prune_backups.sh --apply   actually delete
#
# From here on deploy.sh prunes its own backups (KEEP_BACKUPS=3), so this script
# is for the existing pile only and should not be needed again.
#
# What it keeps, and why:
#
#   nova-hub.db.bak-deploy-*  (newest)  the rollback target for the last deploy
#   nova-hub.db.bak-stagec-*             the state immediately before the Stage C
#                                        retention purge -- the only pre-purge
#                                        copy worth keeping, and the one thing
#                                        here that cannot be reconstructed
#
# What it removes:
#
#   epochcheck/copy.db          a scratch copy taken on 12 Sep to test the
#                               sequence validator against production data
#   nova-hub.db.bak-2026*       a second pre-purge snapshot, four hours older
#                               than the stagec one and superseded by it
#   nova-hub.db.bak-nodelist-*  taken before the nodelist deploy, then superseded
#                               an hour later by the deploy backup
set -e
DATA=/home/novahub/nova-data
APPLY=no
[ "$1" = "--apply" ] && APPLY=yes

KEEP_DEPLOY=$(ls -1t "$DATA"/nova-hub.db.bak-deploy-* 2>/dev/null | head -1)
KEEP_STAGEC=$(ls -1t "$DATA"/nova-hub.db.bak-stagec-* 2>/dev/null | head -1)

echo "=== everything present now ==="
ls -la --time-style=long-iso "$DATA"/nova-hub.db* /home/novahub/epochcheck/copy.db 2>/dev/null
echo
df -h /home | tail -1

echo
echo "=== keeping ==="
for f in "$KEEP_DEPLOY" "$KEEP_STAGEC"; do
  [ -n "$f" ] && echo "  $(basename "$f")  $(du -h "$f" | cut -f1)"
done

# Work out the removal list before touching anything, so the plan printed in a
# dry run is exactly the plan that --apply executes.
REMOVE=""
for f in "$DATA"/nova-hub.db.bak-* /home/novahub/epochcheck/copy.db; do
  [ -f "$f" ] || continue
  [ "$f" = "$KEEP_DEPLOY" ] && continue
  [ "$f" = "$KEEP_STAGEC" ] && continue
  REMOVE="$REMOVE $f"
done

echo
echo "=== removing ==="
if [ -z "$REMOVE" ]; then
  echo "  (nothing -- already pruned)"
  exit 0
fi
TOTAL=0
for f in $REMOVE; do
  SIZE=$(stat -c %s "$f")
  TOTAL=$((TOTAL + SIZE))
  echo "  $(basename "$f")  $(du -h "$f" | cut -f1)"
done
echo "  ---"
echo "  $((TOTAL / 1048576)) MB"

echo
echo "=== are the ones we keep actually intact? ==="
# Verify before deleting, never after. A backup that does not open is not a
# backup, and finding that out once the alternatives are gone is too late.
for f in "$KEEP_DEPLOY" "$KEEP_STAGEC"; do
  [ -n "$f" ] || continue
  RESULT=$(sqlite3 -readonly "$f" "pragma integrity_check;" 2>&1 | head -1)
  echo "  $(basename "$f"): $RESULT"
  if [ "$RESULT" != "ok" ]; then
    echo
    echo "STOP: that backup does not verify. Nothing has been deleted."
    exit 1
  fi
done

if [ "$APPLY" != "yes" ]; then
  echo
  echo "=== dry run. Nothing was deleted. ==="
  echo "Re-run with --apply to remove the files listed above."
  exit 0
fi

echo
echo "=== deleting ==="
for f in $REMOVE; do
  rm -f "$f"
  echo "  removed $(basename "$f")"
done
rmdir /home/novahub/epochcheck 2>/dev/null && echo "  removed empty epochcheck/"

echo
echo "=== after ==="
ls -la --time-style=long-iso "$DATA"/nova-hub.db* 2>/dev/null
echo
df -h /home | tail -1
