### Changed
- **Changelog entries are fragments in `changelog.d/`**, one file per change,
  so open PRs no longer conflict in CHANGELOG.md. `tools/changelog.py release
  <version>` gathers them into CHANGELOG.md in the release commit.
