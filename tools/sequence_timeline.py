"""Show every cycle boundary the model finds on one route, with its context.

For answering "has this route wrapped, or was it reset, and when" against real
data without running the hub or writing anything anywhere.

Export the history from the database first -- arrival order is the whole input,
so the ORDER BY matters:

    sqlite3 -readonly -csv nova-hub.db \
      "SELECT league_id, source_bbs_index, dest_bbs_index, sequence_number
         FROM packets
        ORDER BY league_id, source_bbs_index, dest_bbs_index, uploaded_at, id;" \
      > sequences.csv

    python tools/sequence_timeline.py sequences.csv 3/02/01

Production's route 3/02/01 prints five clean wraps at 999 and two resets, at 494
and 633 -- which is how we know the games really do restart their numbering on a
reset, rather than that being a thing we assumed.
"""

import csv
import sys
from collections import defaultdict

sys.path.insert(0, "/home/lime/nova-hub")
from backend.services.sequence_epoch import (  # noqa: E402
    CYCLE_DROP, RESTART_DROP, SEQUENCE_RANGE, build_timeline,
)

route_wanted = tuple(sys.argv[2].split("/"))
routes = defaultdict(list)
with open(sys.argv[1], newline="") as fh:
    for league, src, dst, seq in csv.reader(fh):
        routes[(league, src, dst)].append(int(seq))

seqs = routes[route_wanted]
print(f"route {'/'.join(route_wanted)}: {len(seqs)} packets, "
      f"{len(set(seqs))} distinct numbers")

# Re-walk with the same rules, narrating each boundary.
high_water = None
epoch = 0
for i, seq in enumerate(seqs):
    if high_water is not None:
        fall = high_water - seq
        big = fall > CYCLE_DROP
        small = fall >= RESTART_DROP and i + 1 < len(seqs) and seq <= seqs[i + 1] < high_water
        if big or small:
            epoch += 1
            kind = "wrap " if high_water >= SEQUENCE_RANGE - 50 else "RESET"
            why = "fall>CYCLE_DROP" if big else "confirmed by look-ahead"
            print(f"  cycle {epoch}: {kind} at index {i:5}  "
                  f"high_water={high_water:3} -> {seq:3}  (fall {fall:3}, {why})")
            print(f"           context: ...{seqs[max(0,i-4):i]} [{seq}] {seqs[i+1:i+5]}...")
            high_water = seq
    high_water = seq if high_water is None else max(high_water, seq)

t = build_timeline(seqs)
print(f"\ntotal cycles: {t.cycles}, resets: {len(t.reset_starts)}")
print(f"span: {t.absolute[0]} .. {max(t.absolute)}  "
      f"({max(t.absolute) - t.absolute[0] + 1} numbers for {len(seqs)} packets)")
