# Changelog fragments

Don't edit `CHANGELOG.md` in a PR. Add one file here instead, named for the
issue it closes, holding the lines the PR would have put under
`## [Unreleased]`:

```
changelog.d/127-scores-ans.md
```

```markdown
### Fixed
- **A league's Scores no longer show `SCORES.ANS`** (#127). The league page
  showed it as a ninth tab, "Scores", alongside the eight rankings.
```

Headings are `### Added`, `### Changed`, `### Deprecated`, `### Removed`,
`### Fixed` and `### Security`. One fragment may use several.

Every PR adds its own file, so two open PRs never touch the same lines and
never conflict over the changelog.

At release, `tools/changelog.py release <version>` gathers the fragments into a
`## [<version>] - <date>` section of `CHANGELOG.md`, one heading each, and
deletes them. Commit both changes in the release commit.
`tools/changelog.py preview` shows what that section will say, and
`tools/changelog.py check` checks the fragments. CI runs the check as a test.

The order of a release:

1. **Release commit on main:** `tools/changelog.py release <version>`, then the
   version bump in `backend/core/version.py` and `frontend/package.json`, then
   `export_api_docs.py`.
2. **Tag it**, once CI is green.
3. **`tools/release.sh <tag>`**, from any checkout, at any time after. It reads
   the notes from the tag's `CHANGELOG.md`, not the working tree's, so the
   checkout can be on any branch or behind.
