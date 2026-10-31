"""Read a Falcon's Eye packet: BRE's container, FE's own record types.

FE writes its packets exactly as BRE does (bre_packet): a 12-byte header,
records of type, length, ends and CRC over a zero-run-compressed payload, and a
0x0F trailer. Shown on the rig (900F, Oct 2026): bre_packet.parse reads every
FE packet the games wrote there with every CRC good.

Only the type numbers differ. FE.EXE holds its table of record names, in type
order from "Global Reset" to "Spy Report", as BRE.EXE does; the order was
checked on the rig against the sizes FE's /DETAILED transcript prints for the
records it wrote (Config Update 232 bytes, Routing List 257, Dummy Data 2,
Recon Request 2, Recon Update 1194).

What is not mapped yet is the inside of FE's war records -- Attack, Attack
Result, Spell, Spell Result, Trade Deal, Message -- so the hub traces nothing in
an FE league. Production carried a few in 2025 (seven 2,903-byte attacks and
their results, four 21-byte spells), too few to map without the rig. A Recon
Update names its counties, but not where BRE's does.
"""
from typing import List

from backend.services.bre_packet import PacketFormatError, Record  # noqa: F401
from backend.services import bre_packet

RECORD_TYPES = {
    0x01: "Global Reset",       # 232; the Config Update's layout, as BRE's 0x10 is
    0x02: "Config Update",      # 232
    0x03: "Player List",
    0x04: "Routing List",       # 257
    0x05: "Nodelist",
    0x06: "Time Check",         # 8
    0x07: "Problem Data",
    0x08: "Dummy Data",         # 2
    0x09: "Recon Request",      # 2
    0x0A: "Recon Update",       # 1194
    0x0B: "News",               # 258
    0x0C: "Report",
    0x0D: "Message",            # 2657
    0x0E: "Trade Deal",
    0x0F: "Trade Result",
    0x10: "Attack",             # 2903
    0x11: "Attack Result",      # 2903
    0x12: "Spell",              # 21
    0x13: "Spell Result",       # 21
    0x14: "Spy Report",
}

MESSAGE = 0x0D      # player mail: never to be decoded for display, as in BRE


def parse(packet: bytes) -> List[Record]:
    return bre_packet.parse(packet, RECORD_TYPES)
