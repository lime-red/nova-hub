"""The 8.3 guard.

A DOS path with an over-long component does not produce an error anywhere. The
16-bit Turbo Pascal the games are built with simply fails to traverse it, so the
game runs to completion, prints every phase marker, and ingests nothing at all.
That is the R-3 failure mode. The hub's own check of its games is preflight
(backend/services/preflight.py); this is the rule for the rig's member installs.
"""

# The path rule itself lives in sr-harness, which drives the games for the rig.
from sr_harness.dospath import bbs_cfg_inbound, dos_path_problems  # noqa: F401
