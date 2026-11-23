#!/usr/bin/env python3
"""Versioning, changelog and release-commit tooling for grayjay-link.

Rules (see CONTRIBUTING.md):

  Commit messages follow Conventional Commits:  <type>(<scope>)!: <subject>
    feat                                      -> "Features" section, minor bump
    fix, perf                                 -> "Fixes" section,    patch bump
    chore, docs, refactor, test, build, ci,
    style, revert                             -> "Chores" section,   patch bump
    "!" after the type/scope, or a "BREAKING CHANGE:" footer -> major bump
    (while the version is 0.x, a breaking change bumps the minor version)

  The project version lives in grayjay_link/__init__.py and
  plasmoid/metadata.json. The Firefox extension (firefox/manifest.json) has
  its own version: a release sets it to the release version only when
  something under firefox/ changed since the previous release, so unchanged
  extensions are not re-submitted to addons.mozilla.org.

Subcommands:
  prepare [--version X.Y.Z] [--body-file F] [--dry-run]
        Compute the next version from the commits since the last vX.Y.Z tag,
        update the version files, prepend the CHANGELOG.md section, and create
        the release commit "chore(release): X.Y.Z". Pushing that commit to main
        makes CI tag and publish the release.
  notes X.Y.Z        Print the CHANGELOG.md section of a version (release notes).
  check [X.Y.Z]      Verify the version files agree (and equal X.Y.Z if given).
  lint REV|RANGE     Verify commit messages follow the rules above.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
DEFAULT_REPO_URL = "https://github.com/vintoware/grayjay-link"

TYPES = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Fixes",
    "chore": "Chores",
    "docs": "Chores",
    "refactor": "Chores",
    "test": "Chores",
    "build": "Chores",
    "ci": "Chores",
    "style": "Chores",
    "revert": "Chores",
}
SECTIONS = ["Features", "Fixes", "Chores"]
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[\w./-]+)\))?(?P<bang>!)?: (?P<subject>\S.*)$")
BREAKING_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE: (?P<text>.+)$", re.M)
RELEASE_RE = re.compile(r"^chore\(release\): (?P<version>\d+\.\d+\.\d+)$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

CHANGELOG_HEADER = """# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org);
sections are generated from [Conventional Commits](https://www.conventionalcommits.org) by
`tools/release.py` (see CONTRIBUTING.md).

"""


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


# --- version files -------------------------------------------------------------

CORE_VERSION_FILES = {
    "grayjay_link/__init__.py": (r'^__version__ = "(?P<v>[^"]+)"$', '__version__ = "{v}"'),
    "plasmoid/metadata.json": (r'^(?P<pre>\s*"Version": )"(?P<v>[^"]+)"', '{pre}"{v}"'),
}
EXTENSION_DIR = "firefox"
EXTENSION_VERSION_FILE = {
    "firefox/manifest.json": (r'^(?P<pre>  "version": )"(?P<v>[^"]+)"', '{pre}"{v}"'),
}
VERSION_FILES = {**CORE_VERSION_FILES, **EXTENSION_VERSION_FILE}


def read_versions() -> dict[str, str]:
    found = {}
    for rel, (pattern, _) in VERSION_FILES.items():
        m = re.search(pattern, (ROOT / rel).read_text(), re.M)
        if not m:
            raise SystemExit(f"{rel}: version not found")
        found[rel] = m.group("v")
    return found


def write_versions(version: str, extension: bool) -> None:
    files = VERSION_FILES if extension else CORE_VERSION_FILES
    for rel, (pattern, template) in files.items():
        path = ROOT / rel
        text, n = re.subn(pattern, lambda m: template.format(v=version, pre=m.groupdict().get("pre", "")),
                          path.read_text(), count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"{rel}: could not update version")
        path.write_text(text)
    json.loads((ROOT / "plasmoid/metadata.json").read_text())
    json.loads((ROOT / "firefox/manifest.json").read_text())


def current_version() -> str:
    found = read_versions()
    versions = {found[rel] for rel in CORE_VERSION_FILES}
    if len(versions) != 1:
        raise SystemExit(f"version files disagree: {found}")
    version = versions.pop()
    ext = found["firefox/manifest.json"]
    if not VERSION_RE.match(ext) or version_tuple(ext) > version_tuple(version):
        raise SystemExit(f"extension version {ext} must be a version no newer than {version}")
    return version


def extension_version() -> str:
    return read_versions()["firefox/manifest.json"]


def extension_changed_since(tag: str | None) -> bool:
    if tag is None:
        return True
    return subprocess.run(["git", "diff", "--quiet", tag, "HEAD", "--", EXTENSION_DIR], cwd=ROOT).returncode != 0


# --- commits -------------------------------------------------------------------


class Commit:
    def __init__(self, sha: str, subject: str, body: str):
        self.sha, self.subject, self.body = sha, subject, body
        m = HEADER_RE.match(subject)
        self.valid = bool(m) and m.group("type") in TYPES
        self.type = m.group("type") if m else None
        self.scope = m.group("scope") if m else None
        self.text = m.group("subject") if m else subject
        footer = BREAKING_FOOTER_RE.search(body)
        self.breaking = bool(m and m.group("bang")) or bool(footer)
        self.breaking_text = footer.group("text") if footer else self.text
        self.is_release = bool(RELEASE_RE.match(subject))


def commits(rev_range: str) -> list[Commit]:
    out = git("log", "--no-merges", "--reverse", "--format=%H%x00%s%x00%b%x1e", rev_range)
    result = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, subject, body = record.split("\x00")
            result.append(Commit(sha, subject, body))
    return result


def last_tag() -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*.[0-9]*.[0-9]*").strip()
    except subprocess.CalledProcessError:
        return None


def lint_message(subject: str) -> str | None:
    if subject.startswith('Revert "') or subject.startswith("Merge "):
        return None
    m = HEADER_RE.match(subject)
    if not m:
        return "not '<type>(<scope>)!: <subject>'"
    if m.group("type") not in TYPES:
        return f"unknown type {m.group('type')!r} (allowed: {', '.join(TYPES)})"
    return None


# --- versioning ----------------------------------------------------------------


def next_version(version: str, changes: list[Commit]) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if any(c.breaking for c in changes):
        return f"{major}.{minor + 1}.0" if major == 0 else f"{major + 1}.0.0"
    if any(c.type == "feat" for c in changes):
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


# --- changelog -----------------------------------------------------------------


def repo_url() -> str:
    try:
        url = git("remote", "get-url", "origin").strip()
    except (subprocess.CalledProcessError, OSError):  # no repo, no remote, or no git at all
        return DEFAULT_REPO_URL
    m = re.match(r"(?:git@github\.com:|https://github\.com/)(?P<path>[^/]+/[^/]+?)(?:\.git)?$", url)
    return f"https://github.com/{m.group('path')}" if m else DEFAULT_REPO_URL


def render_section(version: str, previous: str | None, changes: list[Commit], body: str | None,
                   extension: str | None = None) -> str:
    url = repo_url()
    date = datetime.date.today().isoformat()
    title = f"[{version}]({url}/compare/v{previous}...v{version})" if previous else version
    out = [f"## {title} ({date})", ""]
    if extension == version:
        out += [f"Firefox extension: new version {version}.", ""]
    elif extension:
        out += [f"Firefox extension: unchanged ({extension}).", ""]
    if body is not None:
        out += [body.strip(), ""]
        return "\n".join(out) + "\n"

    def line(c: Commit, text: str) -> str:
        scope = f"**{c.scope}:** " if c.scope else ""
        return f"- {scope}{text} ([`{c.sha[:7]}`]({url}/commit/{c.sha}))"

    breaking = [c for c in changes if c.breaking]
    if breaking:
        out += ["### ⚠ Breaking changes", ""] + [line(c, c.breaking_text) for c in breaking] + [""]
    for section in SECTIONS:
        items = [line(c, c.text) for c in changes if TYPES[c.type] == section]
        if items:
            out += [f"### {section}", ""] + items + [""]
    return "\n".join(out) + "\n"


def prepend_changelog(section: str) -> None:
    text = CHANGELOG.read_text() if CHANGELOG.exists() else CHANGELOG_HEADER
    idx = text.find("\n## ")
    if idx == -1:
        text = text.rstrip("\n") + "\n\n" + section
    else:
        text = text[:idx + 1] + section + "\n" + text[idx + 1:]
    CHANGELOG.write_text(text)


def section_of(version: str) -> str:
    text = CHANGELOG.read_text()
    m = re.search(rf"^## \[?{re.escape(version)}\]?[ (].*?$\n(?P<body>.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group("body").strip() + "\n"


# --- subcommands ---------------------------------------------------------------


def cmd_prepare(args) -> int:
    if git("status", "--porcelain").strip():
        raise SystemExit("working tree is not clean; commit or stash first")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != "main":
        raise SystemExit(f"releases are made from main (on {branch})")
    current = current_version()
    tag = last_tag()
    changes = [c for c in commits(f"{tag}..HEAD" if tag else "HEAD") if not c.is_release]
    if not changes:
        raise SystemExit(f"no commits since {tag}; nothing to release")
    invalid = [c for c in changes if not c.valid]
    if invalid and args.body_file is None:
        for c in invalid:
            print(f"  {c.sha[:7]} {c.subject}", file=sys.stderr)
        raise SystemExit("commits above don't follow Conventional Commits; "
                         "pass --body-file with hand-written notes to release anyway")
    previous = tag[1:] if tag else None
    base = previous or current
    version = args.version or next_version(base, [c for c in changes if c.valid])
    if not VERSION_RE.match(version):
        raise SystemExit(f"invalid version {version!r}")
    if previous and version_tuple(version) <= version_tuple(previous):
        raise SystemExit(f"{version} is not newer than the last release {previous}")
    body = pathlib.Path(args.body_file).read_text() if args.body_file else None
    ext_changed = extension_changed_since(tag)
    section = render_section(version, previous, changes, body,
                             extension=version if ext_changed else extension_version())

    print(f"Releasing {version} (last release: {previous or 'none'}, {len(changes)} commits)\n")
    print(section)
    if args.dry_run:
        return 0
    write_versions(version, extension=ext_changed)
    prepend_changelog(section)
    git("add", "CHANGELOG.md", *VERSION_FILES)
    git("commit", "-q", "-m", f"chore(release): {version}")
    print(f"Created the release commit for {version}. Publish it with:\n\n    git push origin main\n")
    return 0


def cmd_notes(args) -> int:
    sys.stdout.write(section_of(args.version))
    return 0


def cmd_check(args) -> int:
    version = current_version()
    if args.version and args.version != version:
        raise SystemExit(f"version files say {version}, expected {args.version}")
    print(version)
    return 0


def cmd_lint(args) -> int:
    rev = args.range if ".." in args.range else f"{args.range}^!"
    bad = []
    for c in commits(rev):
        problem = lint_message(c.subject)
        if problem:
            bad.append(f"{c.sha[:7]} {c.subject!r}: {problem}")
    for b in bad:
        print(b, file=sys.stderr)
    if bad:
        print("\nCommit messages must follow Conventional Commits; see CONTRIBUTING.md.", file=sys.stderr)
        return 1
    print("commit messages OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--version", help="override the computed version")
    p.add_argument("--body-file", help="use this text as the changelog section body")
    p.add_argument("--dry-run", action="store_true")
    n = sub.add_parser("notes")
    n.add_argument("version")
    c = sub.add_parser("check")
    c.add_argument("version", nargs="?")
    lint = sub.add_parser("lint")
    lint.add_argument("range")
    args = ap.parse_args(argv)
    return {"prepare": cmd_prepare, "notes": cmd_notes, "check": cmd_check, "lint": cmd_lint}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
