"""Check business-rule files and their citations against the reference clone.

Fails (exit code 1) when:

* the reference clone is not at the pinned commit, or has local changes
* a rule file is not valid YAML or does not match docs/current-state/rules/_schema.yaml
* a rule has no citation
* a cited file does not exist in the clone at the pinned commit
* a cited line (or the end of a cited range) is past the end of the file

Files whose names start with ``_`` (such as ``_schema.yaml``) are not rule files and are
skipped. A folder with no rule files passes.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator

PINNED_COMMIT = "59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = REPO_ROOT / "docs" / "current-state" / "rules" / "_schema.yaml"
CITATION_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")


@dataclass(frozen=True)
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def git(clone: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def check_clone(clone: Path, expected_commit: str) -> list[Problem]:
    where = str(clone)
    if not clone.is_dir():
        return [Problem(where, "reference clone not found")]
    try:
        head = git(clone, "rev-parse", "HEAD").strip()
        dirty = git(clone, "status", "--porcelain", "--untracked-files=normal").strip()
    except RuntimeError as exc:
        return [Problem(where, f"not a git clone: {exc}")]
    problems = []
    if head != expected_commit:
        problems.append(Problem(where, f"clone is at commit {head}, expected {expected_commit}"))
    if dirty:
        problems.append(Problem(where, "clone has local changes; citations must refer to the pinned commit as published"))
    return problems


class LineCounter:
    """Line counts of files tracked at HEAD in the clone, read once each."""

    def __init__(self, clone: Path):
        self.clone = clone
        self.tracked = set(git(clone, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
        self.cache: dict[str, int] = {}

    def lines(self, rel: str) -> int | None:
        if rel not in self.tracked:
            return None
        if rel not in self.cache:
            data = (self.clone / rel).read_bytes()
            self.cache[rel] = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
        return self.cache[rel]


def check_citation(citation: object, counter: LineCounter) -> str | None:
    """Return a problem description, or None if the citation is good."""
    if not isinstance(citation, str):
        return f"citation {citation!r} is not a string"
    m = CITATION_RE.match(citation)
    if not m:
        return f"citation '{citation}' is not path:line or path:start-end"
    path = PurePosixPath(m["path"])
    if path.is_absolute() or ".." in path.parts or m["path"].startswith("~"):
        return f"citation '{citation}' must be relative to the clone root"
    start, end = int(m["start"]), int(m["end"] or m["start"])
    if end < start:
        return f"citation '{citation}' has a range that ends before it starts"
    count = counter.lines(m["path"])
    if count is None:
        return f"citation '{citation}': file {m['path']} does not exist in the reference clone"
    if end > count:
        return f"citation '{citation}': line {end} is past the end of {m['path']} ({count} lines)"
    return None


def rule_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix in {".yaml", ".yml"} and not p.name.startswith("_")
    )


def check_file(path: Path, validator: Draft202012Validator, counter: LineCounter | None, seen_ids: dict[str, str]) -> tuple[list[Problem], int, int]:
    """Return (problems, rules checked, citations checked)."""
    where = path.as_posix()
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return [Problem(where, f"not valid YAML: {exc}")], 0, 0

    problems = [
        Problem(where, f"schema: {'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}")
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    ]
    if not isinstance(doc, dict):
        return problems, 0, 0
    if isinstance(doc.get("program"), str) and doc["program"] != path.stem:
        problems.append(Problem(where, f"program '{doc['program']}' does not match file name '{path.stem}'"))

    rules = doc.get("rules") if isinstance(doc.get("rules"), list) else []
    n_citations = 0
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        rid = rule.get("id") if isinstance(rule.get("id"), str) else f"rules[{i}]"
        rwhere = f"{where}: {rid}"
        if isinstance(rule.get("id"), str):
            if rid in seen_ids:
                problems.append(Problem(rwhere, f"duplicate rule id, also in {seen_ids[rid]}"))
            else:
                seen_ids[rid] = where
        citations = rule.get("citations")
        if not citations:
            problems.append(Problem(rwhere, "rule has no citation"))
            continue
        if not isinstance(citations, list):
            continue  # reported by the schema
        for c in citations:
            n_citations += 1
            if counter is not None:
                msg = check_citation(c, counter)
                if msg:
                    problems.append(Problem(rwhere, msg))
    return problems, len(rules), n_citations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check business-rule files and their citations against the reference clone.")
    parser.add_argument("rules_dir", type=Path, help="folder of <PROGRAM>.yaml rule files")
    parser.add_argument("clone", type=Path, help="path to the reference clone")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help=f"rule schema (default: {DEFAULT_SCHEMA.relative_to(REPO_ROOT)})")
    parser.add_argument("--expected-commit", default=PINNED_COMMIT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not args.rules_dir.is_dir():
        print(f"error: rules folder {args.rules_dir} not found", file=sys.stderr)
        return 2
    schema = yaml.safe_load(args.schema.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    clone = args.clone.expanduser().resolve()
    problems = check_clone(clone, args.expected_commit)
    counter = LineCounter(clone) if not problems else None

    files = rule_files(args.rules_dir)
    seen_ids: dict[str, str] = {}
    n_rules = n_citations = 0
    for path in files:
        file_problems, r, c = check_file(path, validator, counter, seen_ids)
        problems += file_problems
        n_rules += r
        n_citations += c

    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    summary = f"{args.rules_dir}: {len(files)} rule files, {n_rules} rules, {n_citations} citations"
    if problems:
        print(f"FAILED {summary}: {len(problems)} problems", file=sys.stderr)
        return 1
    print(f"OK {summary}" + (" (no rule files yet)" if not files else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
