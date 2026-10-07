"""Bump the add-on version and turn the changelog into a release section.

Called by .github/workflows/release.yml after a pull request was merged into
main. Only uses the standard library, so the workflow needs no pip install.

Usage:
    release.py --part patch|minor|major --date YYYY-MM-DD --prs prs.json

prs.json is the output of `gh pr list --json number,title,url`, i.e. the pull
requests merged into developement since the previous release. The new version
is printed on stdout.
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

    The hand-written notes under [Unreleased] are kept as they are, the merged
    pull requests are appended as their own list, and an empty [Unreleased]
    heading stays on top for the next round of changes.
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

    return f"{head}### [Unreleased]\n\n{section}\n\n{tail.lstrip(chr(10))}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["patch", "minor", "major"], default="patch")
    parser.add_argument("--date", required=True)
    parser.add_argument("--prs", type=Path, help="JSON list of merged pull requests")
    args = parser.parse_args()

    prs = json.loads(args.prs.read_text(encoding="utf-8")) if args.prs else []

    config_text = CONFIG_FILE.read_text(encoding="utf-8")
    new_version = bump_version(read_version(config_text), args.part)

    CONFIG_FILE.write_text(set_config_version(config_text, new_version), encoding="utf-8")
    VERSION_FILE.write_text(set_module_version(VERSION_FILE.read_text(encoding="utf-8"), new_version), encoding="utf-8")
    CHANGELOG_FILE.write_text(update_changelog(CHANGELOG_FILE.read_text(encoding="utf-8"), new_version, args.date, prs), encoding="utf-8")

    print(new_version)


if __name__ == "__main__":
    main()
