"""Turn a route's repeating 000-999 sequence numbers into numbers that only go up.

BRE and FE number packets 000-999 and then start again. The validator used to
cope with that by *inferring* the wrap: sort the numbers, find the one gap wider
than 500, and splice the list there. That works exactly once, and production has
long since left it behind.

Route 02->01 of league 3 has sent **5,891 packets across 1,000 distinct numbers**
-- six complete cycles. Sorted into a set, those six cycles collapse into a
single flawless run of 000 to 999 with no gap anywhere in it, so the detector
reports nothing missing on that route no matter what was actually lost. Its two
busiest routes have been structurally blind since roughly February. The
inference is not merely fragile; on the data production actually has, it is
wrong.

The information needed to do this properly was never missing -- it was being
discarded. Packets carry `uploaded_at`, so they can be read in the order they
arrived rather than in numerical order, and then the cycles can simply be
**counted**: each time the number drops a long way, that is the next cycle.
Number plus cycle gives an absolute position that rises forever, and every
wrap-around special case in the gap arithmetic disappears, because in that space
999 -> 1000 is a step of one like any other.

**A wrap and a reset look identical and mean opposite things.**

Both restart the numbering, so both end a cycle. But a wrap happens because the
space ran out, and a reset happens because somebody reset the game -- and that
difference decides whether the numbers at the end of the old cycle were ever
issued at all:

- 998, 999, then 000: the space ran out. In absolute terms that is 998, 999,
  1000 -- continuous. If 999 is missing, it is missing, and saying so is right.
- 347, then 000: the game was reset. 348 through 999 were never issued and never
  will be. Absolute terms make that a jump of 653, and reporting 653 lost
  packets would be the old false-alarm problem back again, at scale.

So the boundary is classified, not just counted. The discriminator is where the
old cycle stopped: at the top of the space it is a wrap, short of it a reset.
A reset near the very top is indistinguishable from a wrap, which costs nothing
-- at that point the two are the same event with different names.
"""

from dataclasses import dataclass, field

SEQUENCE_RANGE = 1000

# A fall of more than this below the cycle's high-water mark is taken as the
# numbering restarting, with no corroboration asked for. Nothing in eight months
# of production data arrives anywhere near that late -- the largest forward step
# on any route is 45 -- and demanding confirmation would break the far more
# common case of a route that has just wrapped and sent only one packet since,
# where there is no following arrival to confirm anything and reading it as a
# late packet would misplace it by a thousand.
CYCLE_DROP = 500

# A smaller fall is ambiguous: a game reset from 347 looks exactly like a packet
# turning up out of order. What separates them is what happens next. A restart is
# sustained -- the following packets carry on upward from the low number -- while
# a late arrival is a single dip and the route resumes where it left off. So a
# fall of at least this much is only a *candidate*, confirmed by looking at the
# next arrival. Below it nothing is claimed; a reset after five packets is not
# worth detecting and not safely detectable.
RESTART_DROP = 20

# How close to the end of the space a cycle must stop for its ending to count as
# the space running out rather than somebody resetting the game. Generous on
# purpose: a dense route stops at 999, a route striding by two might stop at 998,
# and a route that lost its last few packets before the roll might stop at 995.
# Anything below this is treated as a reset, which is the quieter reading.
WRAP_TAIL = 50


@dataclass(frozen=True)
class Timeline:
    """A route's history in a sequence space that only ever increases.

    `absolute` is parallel to the arrivals it was built from: same length, same
    order, one absolute position each.

    `reset_starts` holds the absolute positions that begin a cycle *after a
    reset*. The gap finder must not measure across those -- the distance either
    side of one is an artefact of the game restarting, not packets going astray.
    Positions beginning a cycle after an ordinary wrap are deliberately absent:
    those need no special handling, which is the whole point of counting cycles
    instead of guessing at them.
    """

    absolute: list[int] = field(default_factory=list)
    reset_starts: set[int] = field(default_factory=set)
    cycles: int = 1

    def __len__(self) -> int:
        return len(self.absolute)


def _restart_is_sustained(sequences, index: int, high_water: int) -> bool:
    """Does the route carry on from here, or was this one packet arriving late?

    A restart keeps going up from the low number. A late arrival is a dip the
    route immediately climbs back out of, returning to where it already was. One
    arrival of look-ahead is enough to tell those apart, and an ambiguous drop at
    the very end of the history is left alone -- there is no evidence yet, and
    calling it a restart would rewrite the whole route's numbering on a guess.
    """
    if index + 1 >= len(sequences):
        return False
    following = sequences[index + 1]
    return sequences[index] <= following < high_water


def build_timeline(sequences) -> Timeline:
    """Absolute positions for one route's sequence numbers, in arrival order.

    `sequences` MUST be in arrival order -- that is the input this depends on,
    and sorting it first would discard the very thing being read. Production
    supports the assumption: across 18,000 consecutive arrivals only four go
    backwards and four repeat, and none of those falls far enough to be mistaken
    for a restart.

    Comparison is against the cycle's high-water mark rather than the previous
    arrival, so one late packet cannot hide a restart that follows it.
    """
    sequences = list(sequences)
    absolute: list[int] = []
    reset_starts: set[int] = set()
    epoch = 0
    high_water = None

    for index, seq in enumerate(sequences):
        if high_water is not None:
            fall = high_water - seq
            restarted = fall > CYCLE_DROP or (
                fall >= RESTART_DROP and _restart_is_sustained(sequences, index, high_water)
            )
            if restarted:
                epoch += 1
                # Where the old cycle *stopped* is what says which kind of ending
                # it was. Where the new one starts says nothing: both start low.
                if high_water < SEQUENCE_RANGE - WRAP_TAIL:
                    reset_starts.add(epoch * SEQUENCE_RANGE + seq)
                high_water = seq

        absolute.append(epoch * SEQUENCE_RANGE + seq)
        high_water = seq if high_water is None else max(high_water, seq)

    return Timeline(absolute=absolute, reset_starts=reset_starts, cycles=epoch + 1)
