"""The rig's view of a dosemu transcript.

The parser itself is product code now -- `backend/services/transcript_service` --
because the hub reads transcripts for the same reason the rig does: to find out
what actually moved between nodes. Keeping a second copy here meant a fix landed
in one and not the other, which is how the run-together record bug survived in
the tool long after it was understood.
"""
from backend.services.transcript_service import (  # noqa: F401  (re-exported)
    ANSI,
    ITEM,
    MAIL,
    PHASE,
    TOTAL,
    Transcript,
    clean,
    parse,
)

__all__ = ["ANSI", "ITEM", "MAIL", "PHASE", "TOTAL", "Transcript", "clean", "parse"]
