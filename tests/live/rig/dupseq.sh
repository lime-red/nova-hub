#!/usr/bin/env bash
# Does a receiving BRE accept a packet whose sequence number it has already seen?
#
# Production's hub has been round the 000-999 numbering on its outbound routes
# and BRE now reissues filenames it used before -- 015B0102.745 was first written
# in February and was rewritten and re-served today. The hub handles that
# deliberately (it recycles the database row and resets is_downloaded), so
# delivery is fine at the hub end. The open question is the far end: when node 2
# is handed a filename it consumed months ago, does its BRE ingest the new
# contents or discard them as a duplicate?
#
# If it discards them, hub-originated mail has been silently dropping since the
# routes wrapped, and nothing anywhere would say so.
#
# This grinds the whole thing out unattended: restore both installs from
# fixtures, make two genuinely different packets for node 2, feed the first,
# then feed the second wearing the first's filename, and read what the game says.
set -euo pipefail

SPIKES=/home/lime/novatest-spikes
CONF=$SPIKES/conf/vgaterm.conf
WORK=/tmp/dupseq
LOGS=$WORK/logs
HUB_USER=novahub-t
HUB_DIR=b900n01
NODE_USER=node02
NODE_DIR=b900n02

rm -rf "$WORK"; mkdir -p "$LOGS"
chmod 777 "$WORK" "$LOGS"

# dosrun.py owns the pty and returns dosemu's real exit code, unlike script(1).
cp "$SPIKES/dosrun.py" "$WORK/dosrun.py"; chmod 755 "$WORK/dosrun.py"

say() { printf '\n=== %s ===\n' "$*"; }

install_path() { echo "/home/$1/.dosemu/drive_c/bbs/doors/$2"; }

# Write a one-command batch file into an install and run it.
run_cmd() {
    local user=$1 dir=$2 label=$3 cmd=$4
    local path; path=$(install_path "$user" "$dir")
    local bat="RUN.BAT"
    sudo -n -u "$user" sh -c "printf '@ECHO OFF\r\nC:\r\nCD C:\\\\BBS\\\\DOORS\\\\${dir^^}\r\n${cmd}\r\nEXIT\r\n' > '$path/$bat'"
    sudo -n -u "$user" "$WORK/dosrun.py" --log "$LOGS/${label}.log" --timeout 180 \
        -- /usr/bin/dosemu -f "$CONF" -K "$path" -E "$bat" >/dev/null 2>&1 || true
    # dosemu leaves the transcript raw; strip the escape soup for reading.
    sed -i 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\x1b[()][A-Z0-9]//g; s/\r/\n/g' "$LOGS/${label}.log" 2>/dev/null || true
}

outbound_of() { sudo -n -u "$1" ls "$(install_path "$1" "$2")/OUTBOUND" 2>/dev/null || true; }

say "restore both installs from pristine fixtures"
sudo -n -u "$HUB_USER"  "$SPIKES/fixture.sh" restore "$HUB_DIR"
sudo -n -u "$NODE_USER" "$SPIKES/fixture.sh" restore "$NODE_DIR"

say "hub: force traffic and produce packet A"
# REQUEST creates global recon updates and requests, which guarantees there is
# something to send; PLANETARY then packs and routes it.
run_cmd "$HUB_USER" "$HUB_DIR" 01-request-A "BRE.EXE REQUEST"
run_cmd "$HUB_USER" "$HUB_DIR" 02-planetary-A "BRE.EXE PLANETARY /DETAILED"
A_FILES=$(outbound_of "$HUB_USER" "$HUB_DIR")
echo "hub outbound after A: ${A_FILES:-<none>}"

HUB_PATH=$(install_path "$HUB_USER" "$HUB_DIR")
PKT_A=$(echo "$A_FILES" | grep -i '0102' | head -1 || true)
if [ -z "$PKT_A" ]; then
    echo "FAIL: the hub produced no packet for node 2. Outbound held: ${A_FILES:-<none>}"
    exit 1
fi
sudo -n -u "$HUB_USER" cp "$HUB_PATH/OUTBOUND/$PKT_A" "$WORK/A.pkt"
sudo -n -u "$HUB_USER" rm -f "$HUB_PATH/OUTBOUND/$PKT_A"
sudo -n -u "$HUB_USER" chmod 644 "$WORK/A.pkt"
echo "packet A = $PKT_A ($(stat -c %s "$WORK/A.pkt") bytes, sha $(sha256sum "$WORK/A.pkt" | cut -c1-12))"

say "hub: force more traffic and produce packet B"
run_cmd "$HUB_USER" "$HUB_DIR" 03-request-B "BRE.EXE REQUEST"
run_cmd "$HUB_USER" "$HUB_DIR" 04-planetary-B "BRE.EXE PLANETARY /DETAILED"
B_FILES=$(outbound_of "$HUB_USER" "$HUB_DIR")
echo "hub outbound after B: ${B_FILES:-<none>}"
PKT_B=$(echo "$B_FILES" | grep -i '0102' | head -1 || true)
if [ -z "$PKT_B" ]; then
    echo "FAIL: no second packet for node 2; cannot build the collision."
    exit 1
fi
sudo -n -u "$HUB_USER" cp "$HUB_PATH/OUTBOUND/$PKT_B" "$WORK/B.pkt"
sudo -n -u "$HUB_USER" chmod 644 "$WORK/B.pkt"
echo "packet B = $PKT_B ($(stat -c %s "$WORK/B.pkt") bytes, sha $(sha256sum "$WORK/B.pkt" | cut -c1-12))"

if cmp -s "$WORK/A.pkt" "$WORK/B.pkt"; then
    echo "NOTE: A and B are byte-identical -- the collision test cannot distinguish"
    echo "      ingestion from rejection. Investigate before trusting the result."
fi

NODE_PATH=$(install_path "$NODE_USER" "$NODE_DIR")

say "node 2: ingest packet A (named $PKT_A)"
sudo -n -u "$NODE_USER" cp "$WORK/A.pkt" "$NODE_PATH/INBOUND/$PKT_A"
run_cmd "$NODE_USER" "$NODE_DIR" 05-node-ingest-A "BRE.EXE PLANETARY /DETAILED"
echo "--- what the game reported ---"
grep -oE "DeCompress:[^C]*|Processing Incoming Data from Node [0-9]+" "$LOGS/05-node-ingest-A.log" | head -10 || echo "(no DeCompress lines)"
echo "inbound left behind: $(sudo -n -u "$NODE_USER" ls "$NODE_PATH/INBOUND" | tr '\n' ' ')"
echo "DUPES.TXT: $(sudo -n -u "$NODE_USER" sh -c "wc -c < '$NODE_PATH/DUPES.TXT' 2>/dev/null || echo absent") bytes"

say "node 2: now hand it packet B wearing packet A's name"
# This is the wrap: same filename, different contents, already consumed once.
sudo -n -u "$NODE_USER" cp "$WORK/B.pkt" "$NODE_PATH/INBOUND/$PKT_A"
run_cmd "$NODE_USER" "$NODE_DIR" 06-node-ingest-B-as-A "BRE.EXE PLANETARY /DETAILED"
echo "--- what the game reported ---"
grep -oE "DeCompress:[^C]*|Processing Incoming Data from Node [0-9]+" "$LOGS/06-node-ingest-B-as-A.log" | head -10 || echo "(no DeCompress lines)"
echo "inbound left behind: $(sudo -n -u "$NODE_USER" ls "$NODE_PATH/INBOUND" | tr '\n' ' ')"
echo "DUPES.TXT: $(sudo -n -u "$NODE_USER" sh -c "wc -c < '$NODE_PATH/DUPES.TXT' 2>/dev/null || echo absent") bytes"
sudo -n -u "$NODE_USER" sh -c "cat '$NODE_PATH/DUPES.TXT' 2>/dev/null | head -5" || true

say "control: hand it packet B under its own name ($PKT_B)"
sudo -n -u "$NODE_USER" cp "$WORK/B.pkt" "$NODE_PATH/INBOUND/$PKT_B"
run_cmd "$NODE_USER" "$NODE_DIR" 07-node-ingest-B-proper "BRE.EXE PLANETARY /DETAILED"
echo "--- what the game reported ---"
grep -oE "DeCompress:[^C]*|Processing Incoming Data from Node [0-9]+" "$LOGS/07-node-ingest-B-proper.log" | head -10 || echo "(no DeCompress lines)"
echo "inbound left behind: $(sudo -n -u "$NODE_USER" ls "$NODE_PATH/INBOUND" | tr '\n' ' ')"

say "transcript sizes (a ~2.6 KB log means the game produced nothing)"
ls -l "$LOGS" | awk '{printf "  %-28s %s\n", $9, $5}'

echo
echo "Logs kept in $LOGS"
