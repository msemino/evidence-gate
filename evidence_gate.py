#!/usr/bin/env python3
"""
evidence-gate — fail the build when a published page claims something the repo does not back.

A portfolio page is a promise: every test name, file path and measured number on it is an
invitation to go and check. This walks that invitation for you, against the repository as it
exists on the remote right now, and exits non-zero when the page is ahead of the code.

Standard library only (Python 3.11+, for tomllib). No install step.

    python evidence_gate.py --rules rules.toml
    python evidence_gate.py --page landing/index.html --repo ../my-repo
    python evidence_gate.py --rules rules.toml --json report.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

FAIL = "FAIL"
WARN = "WARN"


@dataclass
class Finding:
    check: str
    severity: str
    page: str
    line: int
    match: str
    reason: str

    def render(self) -> str:
        mark = "x" if self.severity == FAIL else "!"
        return (
            f"  [{mark}] {self.page}:{self.line}\n"
            f"      {self.check}: {self.match}\n"
            f"      {self.reason}"
        )


# ---------------------------------------------------------------------------------------
# Reading pages. Markup is blanked out rather than stripped, so byte offsets — and therefore
# line numbers — still point at the original file.
# ---------------------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]*>", re.S)
_BLOCK = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def load_page(path: Path) -> tuple[str, str, list[int]]:
    """Return (visible text, raw text, newline offsets)."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm", ".xhtml"}:
        text = _BLOCK.sub(_blank, raw)
        text = _HTML_COMMENT.sub(_blank, text)
        text = _TAG.sub(_blank, text)
    else:
        text = raw
    return text, raw, [i for i, ch in enumerate(raw) if ch == "\n"]


def line_of(offset: int, newlines: list[int]) -> int:
    return bisect.bisect_right(newlines, offset) + 1


# ---------------------------------------------------------------------------------------
# Repository index
# ---------------------------------------------------------------------------------------

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
SOURCE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".go", ".rs", ".rb",
    ".java", ".c", ".h", ".cpp", ".sql", ".toml", ".yml", ".yaml", ".ipynb",
}
NOT_SOURCE_NAMES = {"license", "license.md", "licence", "readme.md", "readme", ".gitignore"}


class RepoIndex:
    """What a repository actually contains: which files exist, and what the source says."""

    def __init__(self, root: Path):
        self.root = root
        self.exists = root.is_dir()
        self.rel_paths: set[str] = set()
        self.basenames: set[str] = set()
        self.source_files: list[Path] = []
        self._text_cache: str | None = None
        if self.exists:
            self._walk()

    def _walk(self) -> None:
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                full = Path(dirpath) / name
                self.rel_paths.add(full.relative_to(self.root).as_posix().lower())
                self.basenames.add(name.lower())
                if full.suffix.lower() in SOURCE_SUFFIXES and name.lower() not in NOT_SOURCE_NAMES:
                    self.source_files.append(full)

    def has_path(self, claimed: str) -> bool:
        c = claimed.strip("./").lower()
        if c in self.rel_paths or Path(c).name in self.basenames:
            return True
        return any(p == c or p.endswith("/" + c) for p in self.rel_paths)

    @property
    def source_bytes(self) -> int:
        """Weight of published source. A better signal than file count for telling a case
        study from an implementation: one module that does the work is an implementation."""
        total = 0
        for f in self.source_files:
            try:
                total += f.stat().st_size
            except OSError:
                continue
        return total

    @property
    def source_text(self) -> str:
        if self._text_cache is None:
            chunks = []
            for f in self.source_files:
                try:
                    chunks.append(f.read_text(encoding="utf-8", errors="replace").lower())
                except OSError:
                    continue
            self._text_cache = "\n".join(chunks)
        return self._text_cache


# ---------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------

_PATH_CLAIM = re.compile(
    r"(?<![\w/@.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|ts|tsx|sh|ps1|go|rs|rb|java|sql|toml|ya?ml|ipynb|service|timer))(?![\w/])"
)
_TEST_CLAIM = re.compile(r"(?<![\w.])(test_[a-z0-9_]{2,}|[\w-]+\.test\.[jt]sx?)(?![\w])")
_NUMBER = re.compile(r"\d")
_SOURCE_MARK = re.compile(r"<!--\s*(source|fuente)\s*:", re.I)
_NEGATION = re.compile(r"\b(no|not|never|without|none|neither|nor|sin|nunca|tampoco)\b", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentence_around(text: str, offset: int, span: int = 400) -> str:
    start = max(0, offset - span)
    chunk = text[start : offset + span]
    local = offset - start
    cursor = 0
    for piece in _SENTENCE_SPLIT.split(chunk):
        if cursor <= local <= cursor + len(piece):
            return piece
        cursor += len(piece) + 1
    return chunk


def check_denylist(text: str, newlines: list[int], page: str, rules: list[dict]) -> list[Finding]:
    """Terms that must never reach a public surface — the hardware you don't name, the
    employer you don't name, a phrase that already went out on top of something untrue."""
    out = []
    for rule in rules:
        for m in re.compile(rule["pattern"], re.I).finditer(text):
            out.append(Finding(
                check="denylist",
                severity=rule.get("severity", FAIL),
                page=page,
                line=line_of(m.start(), newlines),
                match=m.group(0).strip(),
                reason=rule.get("reason", "term not allowed on a public surface"),
            ))
    return out


def check_path_claims(
    text: str, newlines: list[int], page: str, repo: RepoIndex, ignore: list[str], severity: str
) -> list[Finding]:
    """A path with a directory in it can only be a file in this repo. A bare name might be a
    third-party library, so it is flagged but does not stop the build. The dividing line is
    how ambiguous the token is, not a hand-maintained list of exceptions."""
    out = []
    scrubbed = _URL.sub(_blank, text)
    ignore_res = [re.compile(p, re.I) for p in ignore]
    seen: set[tuple[str, int]] = set()
    for m in _PATH_CLAIM.finditer(scrubbed):
        claimed = m.group(1)
        if any(r.search(claimed) for r in ignore_res) or repo.has_path(claimed):
            continue
        line = line_of(m.start(), newlines)
        if (claimed, line) in seen:
            continue
        seen.add((claimed, line))
        ambiguous = "/" not in claimed
        out.append(Finding(
            check="path-claim",
            severity=WARN if ambiguous else severity,
            page=page,
            line=line,
            match=claimed,
            reason=(
                f"bare name not found in {repo.root.name}: could be an external library "
                f"or an invented file — check by hand"
                if ambiguous
                else f"the page names this file and it does not exist in {repo.root.name}"
            ),
        ))
    return out


def check_test_claims(
    text: str, newlines: list[int], page: str, repo: RepoIndex, severity: str
) -> list[Finding]:
    """The failure this tool was written for: a page describing a test suite nobody wrote."""
    out = []
    haystack = repo.source_text
    seen: set[tuple[str, int]] = set()
    for m in _TEST_CLAIM.finditer(_URL.sub(_blank, text)):
        name = m.group(1)
        if name.lower() in haystack or repo.has_path(name):
            continue
        line = line_of(m.start(), newlines)
        if (name, line) in seen:
            continue
        seen.add((name, line))
        out.append(Finding(
            check="test-claim",
            severity=severity,
            page=page,
            line=line,
            match=name,
            reason=f"this test is claimed and appears in no source file of {repo.root.name}",
        ))
    return out


def check_measured_claims(
    text: str, raw: str, newlines: list[int], page: str, keywords: list[str], severity: str
) -> list[Finding]:
    """Any number presented as measured needs a source marker nearby. A sentence that denies
    having measured is not a claim to back — that is the honest text the rule wants."""
    out = []
    kw = re.compile("|".join(re.escape(k) for k in keywords), re.I)
    raw_lines = raw.splitlines()
    for m in kw.finditer(text):
        line = line_of(m.start(), newlines)
        window = "\n".join(raw_lines[max(0, line - 3) : line + 2])
        if not _NUMBER.search(window) or _SOURCE_MARK.search(window):
            continue
        if _NEGATION.search(_sentence_around(text, m.start())):
            continue
        out.append(Finding(
            check="measured-claim",
            severity=severity,
            page=page,
            line=line,
            match=m.group(0),
            reason="number presented as measured with no source marker "
                   "(add `<!-- source: path_or_url -->` next to it)",
        ))
    return out


def check_code_promise(
    text: str, newlines: list[int], page: str, repo: RepoIndex,
    phrases: list[str], min_bytes: int, severity: str
) -> list[Finding]:
    """A promise that breaks in one click. An architecture write-up is real work and sells as
    such — what you do not do is let the reader infer an implementation that isn't published."""
    out = []
    if not phrases:
        return out
    size = repo.source_bytes
    if size >= min_bytes:
        return out
    for m in re.compile("|".join(re.escape(p) for p in phrases), re.I).finditer(text):
        out.append(Finding(
            check="code-promise",
            severity=severity,
            page=page,
            line=line_of(m.start(), newlines),
            match=m.group(0).strip(),
            reason=f"the page invites you to read the code and {repo.root.name} publishes "
                   f"{size} bytes of source (expected at least {min_bytes}). "
                   f"If it is a case study, let the button say so",
        ))
    return out


# ---------------------------------------------------------------------------------------
# Fetching the sources fresh. A stale local clone describes software that no longer exists,
# and raw.githubusercontent happily returns 200 for branches that were never there — so the
# remote is the only authority, and the branch it hands back is the one that counts.
# ---------------------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def sync_repo(name: str, remote: str, cache: Path) -> Path:
    dest = cache / name
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = _git("clone", "--depth", "1", "--quiet", remote, str(dest))
        if r.returncode != 0:
            raise RuntimeError(f"clone failed for {name}: {r.stderr.strip()}")
        return dest
    r = _git("fetch", "--depth", "1", "--quiet", "origin", cwd=dest)
    if r.returncode != 0:
        raise RuntimeError(f"fetch failed for {name}: {r.stderr.strip()}")
    _git("reset", "--hard", "--quiet", "FETCH_HEAD", cwd=dest)
    _git("clean", "-qfdx", cwd=dest)
    return dest


def fetch_page(url: str, cache: Path) -> Path:
    """Check what is actually served. What matters is what the person clicking sees."""
    cache.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w.-]+", "_", url.split("://", 1)[-1]).strip("_")[:120]
    dest = cache / f"{slug}.html"
    req = urllib.request.Request(url, headers={"User-Agent": "evidence-gate"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        dest.write_bytes(resp.read())
    return dest


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------

DEFAULTS = {
    "severity": {
        "denylist": FAIL,
        "path-claim": FAIL,
        "test-claim": FAIL,
        "measured-claim": WARN,
        "code-promise": FAIL,
    },
    "measured_keywords": [
        "measured", "benchmark", "coverage", "latency", "p95", "p99", "throughput", "tok/s",
    ],
    "code_promise_phrases": ["view the code", "browse the source", "read the code"],
    "min_source_bytes": 2000,
    "ignore_paths": [
        r"^(styles?|main|app|index|bundle)\.(css|js)$",
        r"^(index|404)\.html$",
        r"\.min\.(js|css)$",
        r"^(package|package-lock|tsconfig|vercel)\.json$",
    ],
}


def run_target(target: dict, denylist: list[dict], settings: dict, cache: Path) -> list[Finding]:
    sev = settings["severity"]
    findings: list[Finding] = []

    repo_path = Path(target["repo"]).expanduser() if target.get("repo") else None
    if target.get("remote"):
        try:
            repo_path = sync_repo(target["name"], target["remote"], cache / "repos")
        except RuntimeError as exc:
            return [Finding("config", FAIL, target["name"], 0, target["remote"], str(exc))]
    if repo_path is None:
        return [Finding("config", FAIL, target["name"], 0, "-",
                        "target defines neither `repo` nor `remote`")]

    repo = RepoIndex(repo_path)
    if not repo.exists:
        return [Finding("config", FAIL, str(repo_path), 0, str(repo_path),
                        "configured repo is not on disk: nothing can be verified")]

    for page_path in target["pages"]:
        if str(page_path).startswith(("http://", "https://")):
            try:
                page = fetch_page(str(page_path), cache / "pages")
            except (urllib.error.URLError, OSError) as exc:
                findings.append(Finding("config", FAIL, str(page_path), 0, str(page_path),
                                        f"could not fetch the published page: {exc}"))
                continue
        else:
            page = Path(page_path).expanduser()
        if not page.is_file():
            findings.append(Finding("config", FAIL, str(page), 0, str(page),
                                    "configured page does not exist"))
            continue

        text, raw, newlines = load_page(page)
        label = str(page_path)
        findings += check_denylist(text, newlines, label, denylist)
        findings += check_path_claims(text, newlines, label, repo,
                                      settings["ignore_paths"], sev["path-claim"])
        findings += check_test_claims(text, newlines, label, repo, sev["test-claim"])
        findings += check_measured_claims(text, raw, newlines, label,
                                          settings["measured_keywords"], sev["measured-claim"])
        findings += check_code_promise(text, newlines, label, repo,
                                       settings["code_promise_phrases"],
                                       settings["min_source_bytes"], sev["code-promise"])
    return findings


def load_rules(path: Path) -> tuple[list[dict], list[dict], dict]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = {**DEFAULTS, **data.get("settings", {})}
    settings["severity"] = {**DEFAULTS["severity"],
                            **data.get("settings", {}).get("severity", {})}
    return data.get("targets", []), data.get("denylist", []), settings


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Verify published claims against the repository that backs them."
    )
    ap.add_argument("--rules", type=Path, help="TOML file with targets and denylist")
    ap.add_argument("--page", type=Path, action="append", help="a single page to verify")
    ap.add_argument("--repo", type=Path, help="repo backing the pages given with --page")
    ap.add_argument("--json", type=Path, help="also write findings as JSON")
    ap.add_argument("--quiet", action="store_true", help="summary only")
    ap.add_argument("--cache", type=Path, default=Path(".cache"),
                    help="where fresh clones and fetched pages are kept")
    ap.add_argument("--only", action="append", help="run only these targets, by name")
    args = ap.parse_args()

    denylist: list[dict] = []
    settings = dict(DEFAULTS)
    targets: list[dict] = []

    if args.rules:
        targets, denylist, settings = load_rules(args.rules)
    if args.page:
        if not args.repo:
            ap.error("--page requires --repo")
        targets.append({"name": args.repo.name,
                        "pages": [str(p) for p in args.page],
                        "repo": str(args.repo)})
    if not targets:
        ap.error("nothing to verify: pass --rules or --page/--repo")

    if args.only:
        wanted = set(args.only)
        targets = [t for t in targets if t["name"] in wanted]
        if not targets:
            ap.error(f"no target matches {sorted(wanted)}")

    all_findings: list[Finding] = []
    for target in targets:
        findings = run_target(target, denylist, settings, args.cache)
        all_findings += findings
        if not args.quiet:
            fails = sum(1 for f in findings if f.severity == FAIL)
            status = "OK" if not findings else f"{fails} FAIL / {len(findings) - fails} WARN"
            print(f"\n== {target['name']} - {status}")
            for f in sorted(findings, key=lambda f: (f.severity != FAIL, f.page, f.line)):
                print(f.render())

    fails = [f for f in all_findings if f.severity == FAIL]
    warns = [f for f in all_findings if f.severity == WARN]
    print(f"\n{'-' * 70}")
    print(f"evidence-gate: {len(targets)} target(s) | {len(fails)} FAIL | {len(warns)} WARN")

    if args.json:
        args.json.write_text(
            json.dumps([asdict(f) for f in all_findings], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"JSON report: {args.json}")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
