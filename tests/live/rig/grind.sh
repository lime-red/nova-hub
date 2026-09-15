#!/usr/bin/env bash
# Grind a BRE install through the whole 000-999 sequence space and watch the roll.
#
# Production's hub is eight cycles deep and reuses filenames it issued months
# before. That is inferred from the data rather than observed, so this walks a
# rig install all the way round to see it happen: what number follows 999, and
# whether the game reissues a name it has used before.
#
# Each cycle is REQUEST (guarantees something to send) then PLANETARY (packs and
# routes it), and the outbound file is removed afterwards the way the hub's
# collection step removes it -- leaving it in place makes BRE rewrite it under
# the same name instead of advancing, which is a different behaviour entirely.
#
# Runs unattended for a couple of hours. Progress goes to the CSV as it happens,
# so it can be read at any point without waiting for the end.
set -uo pipefail

SPIKES=/home/lime/novatest-spikes
CONF=$SPIKES/conf/vgaterm.conf
WORK=/tmp/grind
USER_NAME=novahub-t
DIR=b900n01
PATH_G=/home/$USER_NAME/.dosemu/drive_c/bbs/doors/$DIR
CYCLES=${1:-1100}
CSV=$WORK/cycles.csv

rm -rf "$WORK"; mkdir -p "$WORK"; chmod 777 "$WORK"
cp "$SPIKES/dosrun.py" "$WORK/dosrun.py"; chmod 755 "$WORK/dosrun.py"

run_cmd() {
    sudo -n -u "$USER_NAME" sh -c \
        "printf '@ECHO OFF\r\nC:\r\nCD C:\\\\BBS\\\\DOORS\\\\B900N01\r\n$1\r\nEXIT\r\n' > '$PATH_G/RUN.BAT'"
    sudo -n -u "$USER_NAME" "$WORK/dosrun.py" --log "$WORK/last.log" --timeout 180 \
        -- /usr/bin/dosemu -f "$CONF" -K "$PATH_G" -E RUN.BAT >/dev/null 2>&1
}

echo "restoring $DIR from fixture"
sudo -n -u "$USER_NAME" "$SPIKES/fixture.sh" restore "$DIR" || exit 1

echo "cycle,seq,filename,size,sha12,seen_before,elapsed_s" > "$CSV"
declare -A SEEN
START=$(date +%s)
prev_seq=""

for i in $(seq 1 "$CYCLES"); do
    run_cmd "BRE.EXE REQUEST"
    run_cmd "BRE.EXE PLANETARY /DETAILED"

    f=$(sudo -n -u "$USER_NAME" ls "$PATH_G/OUTBOUND" 2>/dev/null | grep -i '0102' | head -1)
    if [ -z "$f" ]; then
        echo "$i,,,,,no-packet,$(( $(date +%s) - START ))" >> "$CSV"
        continue
    fi

    seq_num="${f##*.}"
    size=$(sudo -n -u "$USER_NAME" stat -c %s "$PATH_G/OUTBOUND/$f")
    sha=$(sudo -n -u "$USER_NAME" sha256sum "$PATH_G/OUTBOUND/$f" | cut -c1-12)
    before="${SEEN[$f]:-no}"
    SEEN[$f]=yes

    echo "$i,$seq_num,$f,$size,$sha,$before,$(( $(date +%s) - START ))" >> "$CSV"

    # Shout about the two things this exists to catch, so a tail of the output
    # finds them without trawling 1100 rows.
    if [ -n "$prev_seq" ] && [ "$((10#$seq_num))" -lt "$((10#$prev_seq))" ]; then
        echo "*** ROLL: $prev_seq -> $seq_num at cycle $i ***"
    fi
    if [ "$before" = "yes" ]; then
        echo "*** REISSUED FILENAME: $f at cycle $i ***"
    fi
    prev_seq="$seq_num"

    # The hub's collection step takes the file away; without that BRE rewrites
    # the same name next cycle rather than moving on.
    sudo -n -u "$USER_NAME" rm -f "$PATH_G/OUTBOUND/$f"

    if [ $((i % 50)) -eq 0 ]; then
        echo "cycle $i: seq=$seq_num elapsed=$(( $(date +%s) - START ))s"
    fi
done

echo "done: $CYCLES cycles in $(( $(date +%s) - START ))s"
