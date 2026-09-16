"""Reading a dosemu transcript for what moved between nodes.

The samples here are written to match the shapes real transcripts take, rather
than copied from production: the point of each one is a specific way the format
misbehaves, and a hand-written line says which way far more clearly than 11 KB
of escape codes would.
"""
from backend.services.transcript_service import Transcript, parse

# What a well-behaved run looks like: markers, one item per line, a mail line.
CLEAN = (
    "■  Processing Incoming Data (May take a while)\n"
    "Processing Incoming Data from Node 2\n"
    "DeCompress:  Old:  56  New:1448  %: 96.1%  Type: Recon Update   2-> 1\n"
    "■  Updating Local Recon Info\n"
    "Compress: Old: 1448  Size:   36  %: 97.5%  Type: Configupdate   1-> 2\n"
    "Outbound mail for SJ Team 1 - Node 2 created.\n"
    "■  Planetary Maintenance Complete\n"
)

# The same run as the emulated screen actually paints it: phases and records run
# together with no separator, and the whole thing is repainted.
PAINTED = (
    "■  Updating Local Recon Info■  Processing Incoming Data (May take a while)"
    "Processing Incoming Data from Node 4"
    "DeCompress:  Old: 177  New:1448  %: 87.8%  Type: Recon Update   4-> 1"
    "DeCompress:  Old:   2  New:   2  %:  0.0%  Type: Dummy Data     4-> 2"
    "DeCompress: \n"
)


def test_a_clean_run_reports_each_item_with_its_direction_and_nodes():
    t = Transcript.from_text(CLEAN)

    assert t.moved() == {
        ("in", "Recon Update", 2, 1),
        ("out", "Configupdate", 1, 2),
    }
    assert t.completed
    assert [m["to_node"] for m in t.mails] == [2]


def test_a_repainted_line_does_not_invent_an_item_type():
    """The bug real production transcripts exposed.

    With a loose type pattern the first record's type runs on into the next
    record, producing the type "Recon Update DeCompress: Old: 2 New: 2 %: 0.0%
    Type: Dummy Data". That is not a type the game has ever emitted, and once
    stored it becomes a row in a table someone is meant to read.
    """
    t = Transcript.from_text(PAINTED)

    assert t.types() == {"Recon Update", "Dummy Data"}
    assert all(":" not in item_type for item_type in t.types())


def test_the_truncated_half_of_a_run_together_line_is_dropped_not_guessed():
    """The trailing bare `DeCompress:` has no type and no nodes.

    Inventing something for it would be worse than losing it -- and nothing is
    really lost, because the repaint carries an intact copy of every record.
    """
    t = Transcript.from_text(PAINTED)

    assert len(t.items) == 2
    assert all(r["src"] and r["dst"] for r in t.items)


def test_repaints_do_not_multiply_the_traffic():
    """Screen repaints are the reason raw line counting is useless.

    Ten paints of one run must read as one run's worth of traffic, or every
    number shown to an admin is wrong by a factor nobody can predict.
    """
    once = Transcript.from_text(CLEAN)
    ten = Transcript.from_text(CLEAN * 10)

    assert len(ten.items) == len(once.items)
    assert ten.moved() == once.moved()


def test_an_item_that_differs_only_in_size_is_kept():
    """De-duplication must not merge two genuinely separate transfers.

    Two Recon Updates from node 2 in one run are two events. They are
    distinguishable only by their sizes, so sizes are part of the identity.
    """
    two = CLEAN + (
        "DeCompress:  Old:  99  New:1448  %: 93.2%  Type: Recon Update   2-> 1\n"
    )
    t = Transcript.from_text(two)

    assert len(t.items) == 3
    assert len([r for r in t.items if r["type"] == "Recon Update"]) == 2


def test_a_run_that_ingested_nothing_says_so():
    """The R-3 signal: every phase completes, no DeCompress line anywhere.

    A game whose BBS.CFG points at a directory that does not exist behaves
    exactly like a game with no mail waiting, and this is the difference.
    """
    silent = (
        "■  Processing Incoming Data (May take a while)\n"
        "■  Planetary Maintenance Complete\n"
    )
    t = Transcript.from_text(silent)

    assert t.completed, "the run did finish -- that is what makes it deceptive"
    assert t.items == []
    assert not t.ingested_from(2)


def test_cp437_transcripts_read_as_well_as_utf8():
    """dosemu's charset setting varies by host; the marker byte differs with it."""
    # 0xFE is the cp437 byte for the marker. It has to be spliced in as a raw
    # byte: encoding the character would just re-encode it as UTF-8.
    as_cp437 = CLEAN.replace("■", "@@").encode("ascii").replace(b"@@", b"\xfe")
    t = Transcript(as_cp437)

    assert t.completed
    assert ("in", "Recon Update", 2, 1) in t.moved()


def test_an_empty_or_missing_transcript_is_not_an_error():
    """Runs predating retention have no transcript at all, and the reader is
    asked for them anyway. It must return nothing rather than raise."""
    assert parse(b"") == ([], [])
    assert Transcript.from_text(None).items == []


def test_phases_are_reported_in_order_without_repeats():
    t = Transcript.from_text(CLEAN * 3)

    assert t.phases[0] == "Processing Incoming Data"
    assert "Planetary Maintenance Complete" in t.phases
