### Fixed
- **The Members count for a reset or order is the number of boards, not
  packets.** An IB thaw writes several packets for each member, so a
  four-member league showed "0 of 40 have it". Each member is now one row,
  showing its packet count, and has the order once it has downloaded all of
  them.
