### Added
- **IB 0.2.9's Full Bank Start and Starting Regions on the reset form.** Both
  can be set in a reset or a rules change. While either is off its default, the
  draft warns until Minimum Game Version is 0.2.9 or later, because a board on
  0.2.8 ignores them, plays different rules and has its packets held.

### Changed
- **A LeagueConfig without these two fields reads as their defaults** (off,
  15), which is what IB omits them for. A reset's read-back now notices a
  Coordinator too old to apply a Starting Regions it was asked for.
- `tests/fixtures/ib_packet_shape.txt` is IB v0.2.9's packet shape, which also
  catches up on the fields v0.2.8 changed.
