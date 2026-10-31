"""Bump the add-on version and turn the changelog into a release section.

Called by .github/workflows/release.yml after a pull request was merged into
main. Only uses the standard library, so the workflow needs no pip install.

Usage:
    release.py --part patch|minor|major --date YYYY-MM-DD --prs prs.json
    release.py --add-unreleased

prs.json is the output of `gh pr list --json number,title,url`, i.e. the pull
requests merged into developement since the previous release. The new version
is printed on stdout.

--add-unreleased only puts an empty [Unreleased] heading back on top of the
changelog. The workflow runs it on developement after carrying the release
over, so the heading exists there but not on main.
"""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.yaml"
VERSION_FILE = ROOT / "rootfs" / "app" / "modules" / "version.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

CONFIG_VERSION_RE = re.compile(r'^version:\s*"(\d+)\.(\d+)\.(\d+)"\s*$', re.MULTILINE)
UNRELEASED_RE = re.compile(r"^### \[Unreleased\][^\n]*\n", re.MULTILINE)
NEXT_SECTION_RE = re.compile(r"^### \[", re.MULTILINE)


def bump_version(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown version part: {part}")


def read_version(config_text: str) -> str:
    match = CONFIG_VERSION_RE.search(config_text)
    if not match:
        raise ValueError('config.yaml has no line like: version: "1.2.3"')
    return ".".join(match.groups())


def set_config_version(config_text: str, version: str) -> str:
    return CONFIG_VERSION_RE.sub(f'version: "{version}"', config_text, count=1)


def set_module_version(version_text: str, version: str) -> str:
    return re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', version_text, count=1)


def format_prs(prs: list) -> str:
    lines = [f"- {pr['title']} ([#{pr['number']}]({pr['url']}))" for pr in sorted(prs, key=lambda p: p["number"])]
    return "\n".join(lines)


def update_changelog(text: str, version: str, date: str, prs: list) -> str:
    """Move the [Unreleased] notes into a new [version] section.

    The hand-written notes under [Unreleased] are kept as they are and the
    merged pull requests are appended as their own list. The [Unreleased]
    heading itself is removed; add_unreleased() puts it back on developement.
    """
    unreleased = UNRELEASED_RE.search(text)
    if unreleased:
        body_start = unreleased.end()
        next_section = NEXT_SECTION_RE.search(text, body_start)
        body_end = next_section.start() if next_section else len(text)
        notes = text[body_start:body_end].strip()
        head, tail = text[:unreleased.start()], text[body_end:]
    else:
        # No [Unreleased] heading: insert before the first release section.
        first = NEXT_SECTION_RE.search(text)
        cut = first.start() if first else len(text)
        notes = ""
        head, tail = text[:cut], text[cut:]
        if not head.endswith("\n\n"):
            head = head.rstrip("\n") + "\n\n"

    parts = [f"### [{version}] - {date}"]
    if notes:
        parts.append(notes)
    if prs:
        parts.append("#### Merged pull requests\n" + format_prs(prs))
    if not notes and not prs:
        parts.append("- No changes recorded.")
    section = "\n".join(parts)

    return f"{head}{section}\n\n{tail.lstrip(chr(10))}"


def add_unreleased(text: str) -> str:
    """Insert an empty [Unreleased] heading before the first release section."""
    if UNRELEASED_RE.search(text):
        return text
    first = NEXT_SECTION_RE.search(text)
    cut = first.start() if first else len(text)
    head = text[:cut].rstrip("\n") + "\n\n"
    return f"{head}### [Unreleased]\n\n{text[cut:]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["patch", "minor", "major"], default="patch")
    parser.add_argument("--date")
    parser.add_argument("--prs", type=Path, help="JSON list of merged pull requests")
    parser.add_argument("--add-unreleased", action="store_true", help="only add an empty [Unreleased] heading")
    args = parser.parse_args()

    if args.add_unreleased:
        CHANGELOG_FILE.write_text(add_unreleased(CHANGELOG_FILE.read_text(encoding="utf-8")), encoding="utf-8")
        return
    if not args.date:
        parser.error("--date is required")

    prs = json.loads(args.prs.read_text(encoding="utf-8")) if args.prs else []

    config_text = CONFIG_FILE.read_text(encoding="utf-8")
    new_version = bump_version(read_version(config_text), args.part)

    CONFIG_FILE.write_text(set_config_version(config_text, new_version), encoding="utf-8")
    VERSION_FILE.write_text(set_module_version(VERSION_FILE.read_text(encoding="utf-8"), new_version), encoding="utf-8")
    CHANGELOG_FILE.write_text(update_changelog(CHANGELOG_FILE.read_text(encoding="utf-8"), new_version, args.date, prs), encoding="utf-8")

    print(new_version)


if __name__ == "__main__":
    main()
