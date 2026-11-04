"""tools/changelog.py: changelog fragments, so PRs stop colliding in CHANGELOG.md."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("changelog_tool", ROOT / "tools" / "changelog.py")
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)

FIXED = "### Fixed\n- **Scores no longer show SCORES.ANS** (#127).\n"
ADDED_AND_FIXED = ("### Added\n- **Code defaults in --diff-template**\n  wrapped line.\n\n"
                   "### Fixed\n- **Another fix** (#9).\n")

CHANGELOG = """# Changelog

## [Unreleased]

## [0.13.0] - 2026-11-05

### Added
- old
"""


def test_a_fragment_parses_by_heading():
    assert changelog.parse(ADDED_AND_FIXED) == {
        "Added": "- **Code defaults in --diff-template**\n  wrapped line.",
        "Fixed": "- **Another fix** (#9).",
    }


@pytest.mark.parametrize("text, message", [
    ("### Fixes\n- x\n", "is not one of"),
    ("- x\n### Fixed\n- y\n", "before the first"),
    ("## [0.14.0]\n### Fixed\n- x\n", "only '### <heading>'"),
    ("### Fixed\n\n", "no entries"),
])
def test_a_malformed_fragment_is_refused(text, message):
    with pytest.raises(changelog.FragmentError, match=message):
        changelog.parse(text, "x.md")


def test_fragments_gather_into_one_section_per_heading_in_order(tmp_path):
    (tmp_path / "127-scores.md").write_text(FIXED)
    (tmp_path / "128-defaults.md").write_text(ADDED_AND_FIXED)
    (tmp_path / "README.md").write_text("not a fragment")
    notes = changelog.gather(changelog.fragment_paths(tmp_path))
    assert notes == (
        "### Added\n- **Code defaults in --diff-template**\n  wrapped line.\n\n"
        "### Fixed\n- **Scores no longer show SCORES.ANS** (#127).\n- **Another fix** (#9).")


def test_release_writes_a_section_under_unreleased():
    out = changelog.release(CHANGELOG, "0.14.0", "### Fixed\n- x", "2026-11-06")
    assert out == """# Changelog

## [Unreleased]

## [0.14.0] - 2026-11-06

### Fixed
- x

## [0.13.0] - 2026-11-05

### Added
- old
"""


def test_release_refuses_a_version_already_there():
    with pytest.raises(changelog.FragmentError, match="already has"):
        changelog.release(CHANGELOG, "0.13.0", "### Fixed\n- x", "2026-11-06")


@pytest.mark.parametrize("text", [
    CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Fixed\n- by hand\n"),
    "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- by hand\n",
])
def test_release_refuses_entries_written_under_unreleased(text):
    with pytest.raises(changelog.FragmentError, match="move them into changelog.d"):
        changelog.release(text, "0.14.0", "### Fixed\n- x", "2026-11-06")


def test_the_repository_fragments_are_well_formed():
    for path in changelog.fragment_paths():
        changelog.parse(path.read_text(), path.name)


def test_nothing_is_written_under_unreleased_by_hand():
    """Entries go in changelog.d/, so two PRs never edit the same lines."""
    changelog.release(changelog.CHANGELOG.read_text(), "999.0.0", "### Fixed\n- x", "2099-01-01")
