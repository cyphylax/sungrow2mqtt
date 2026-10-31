"""Tests for .github/scripts/release.py (version bump + changelog on release)."""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("release_script", _SCRIPT)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

PRS = [
    {"number": 25, "title": "Remove via_device", "url": "https://example.test/pull/25"},
    {"number": 24, "title": "Stop on missing serial", "url": "https://example.test/pull/24"},
]

CHANGELOG = """# Title

## Changelog

### [Unreleased]
#### Fixed
- Something fixed.

### [1.2.1] - 2026-08-11
#### Fixed
- Older fix.
"""


@pytest.mark.parametrize(
    "part, expected",
    [("patch", "1.2.2"), ("minor", "1.3.0"), ("major", "2.0.0")],
)
def test_bump_version(part, expected):
    assert release.bump_version("1.2.1", part) == expected


def test_bump_version_rejects_unknown_part():
    with pytest.raises(ValueError):
        release.bump_version("1.2.1", "build")


def test_config_version_roundtrip():
    text = 'name: x\nversion: "1.2.1"\nslug: x\n'
    assert release.read_version(text) == "1.2.1"
    assert release.set_config_version(text, "1.3.0") == 'name: x\nversion: "1.3.0"\nslug: x\n'


def test_module_version():
    assert release.set_module_version('__version__ = "1.2.1"\n', "1.2.2") == '__version__ = "1.2.2"\n'


def test_changelog_moves_unreleased_notes_and_lists_prs():
    result = release.update_changelog(CHANGELOG, "1.2.2", "2026-10-07", PRS)
    assert result == """# Title

## Changelog

### [1.2.2] - 2026-10-07
#### Fixed
- Something fixed.
#### Merged pull requests
- Stop on missing serial ([#24](https://example.test/pull/24))
- Remove via_device ([#25](https://example.test/pull/25))

### [1.2.1] - 2026-08-11
#### Fixed
- Older fix.
"""


def test_changelog_without_notes_or_prs():
    text = "## Changelog\n\n### [Unreleased]\n\n### [1.0.0] - 2026-01-01\n- First.\n"
    result = release.update_changelog(text, "1.0.1", "2026-10-07", [])
    assert result == "## Changelog\n\n### [1.0.1] - 2026-10-07\n- No changes recorded.\n\n### [1.0.0] - 2026-01-01\n- First.\n"


def test_changelog_without_unreleased_heading():
    text = "## Changelog\n\n### [1.0.0] - 2026-01-01\n- First.\n"
    result = release.update_changelog(text, "1.0.1", "2026-10-07", PRS[:1])
    assert result == (
        "## Changelog\n\n### [1.0.1] - 2026-10-07\n"
        "#### Merged pull requests\n- Remove via_device ([#25](https://example.test/pull/25))\n\n"
        "### [1.0.0] - 2026-01-01\n- First.\n"
    )


def test_add_unreleased_inserts_heading_before_first_release():
    text = "## Changelog\n\n### [1.0.1] - 2026-10-07\n- Fix.\n"
    assert release.add_unreleased(text) == "## Changelog\n\n### [Unreleased]\n\n### [1.0.1] - 2026-10-07\n- Fix.\n"


def test_add_unreleased_keeps_existing_heading():
    text = "## Changelog\n\n### [Unreleased]\n- Pending.\n\n### [1.0.0] - 2026-01-01\n"
    assert release.add_unreleased(text) == text
