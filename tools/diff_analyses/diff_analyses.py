"""Compare Analyst A's and Analyst B's rule files and report where they disagree.

Rule IDs are local to each analyst, so rules are matched by citation: an A rule and a
B rule are counterparts when they cite overlapping lines of the same file.

The report lists:

* rules found by only one analyst (no rule from the other analyst cites an overlapping line)
* counterpart pairs whose statements may describe different behaviour

"Different behaviour" is decided by a deliberately cautious text heuristic, because a
missed disagreement costs more than a false alarm. A pair is flagged when any of these hold:

* statement similarity (word sequence match, 0 to 1) is below ``--threshold``
* the numbers or quoted values in the two statements differ
* one statement is negated (not, no, never, ...) where the other is not

A human or reviewer agent decides each flagged pair. Run ``tools/check_citations`` on
both folders first; this tool does not validate the schema.

Exit codes: 0 no differences, 1 differences found, 2 usage error.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_A = REPO_ROOT / "docs" / "current-state" / "analysis-a" / "rules"
DEFAULT_B = REPO_ROOT / "docs" / "current-state" / "analysis-b" / "rules"
CITATION_RE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?$")
WORD_RE = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
VALUE_RE = re.compile(r"'[^']*'|\"[^\"]*\"|-?\b[0-9]+(?:[.,][0-9]+)*\b")
NEGATIONS = {"not", "no", "never", "none", "cannot", "neither", "nor", "without", "unless"}
STOPWORDS = {"a", "an", "the", "is", "are", "be", "been", "it", "its", "of", "to", "in", "on", "and", "or", "for", "by", "with", "that", "this", "as", "at", "then", "when", "if"}


@dataclass(frozen=True)
class Citation:
    path: str
    start: int
    end: int

    def overlaps(self, other: Citation, tolerance: int) -> bool:
        return self.path == other.path and self.start - tolerance <= other.end and other.start - tolerance <= self.end

    def __str__(self) -> str:
        return f"{self.path}:{self.start}" + (f"-{self.end}" if self.end != self.start else "")


@dataclass
class Rule:
    analyst: str
    file: str
    id: str
    statement: str
    category: str
    citations: list[Citation]

    @property
    def label(self) -> str:
        return f"{self.analyst} {self.id}"


def parse_citation(text: object) -> Citation | None:
    m = CITATION_RE.match(text) if isinstance(text, str) else None
    if not m:
        return None
    start = int(m["start"])
    return Citation(m["path"], start, int(m["end"] or start))


def load_rules(folder: Path, analyst: str) -> tuple[list[Rule], list[str]]:
    rules, warnings = [], []
    for path in sorted(folder.glob("*.y*ml")):
        if path.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            warnings.append(f"{path}: not valid YAML, skipped ({exc})")
            continue
        for i, r in enumerate(doc.get("rules") or [] if isinstance(doc, dict) else []):
            if not isinstance(r, dict):
                continue
            cites = [c for c in map(parse_citation, r.get("citations") or []) if c]
            rid = str(r.get("id", f"{path.stem}[{i}]"))
            if not cites:
                warnings.append(f"{path}: {rid} has no usable citation and cannot be matched")
            rules.append(Rule(analyst, path.name, rid, " ".join(str(r.get("statement", "")).split()), str(r.get("category", "")), cites))
    return rules, warnings


def words(statement: str) -> list[str]:
    return [w for w in WORD_RE.findall(statement.lower()) if w not in STOPWORDS]


def values(statement: str) -> set[str]:
    return {v.strip("'\"").strip().lower() for v in VALUE_RE.findall(statement)}


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, words(a), words(b), autojunk=False).ratio()


def negated(statement: str) -> bool:
    lowered = statement.lower()
    return bool(NEGATIONS & set(WORD_RE.findall(lowered))) or "n't" in lowered


def overlapping(rule: Rule, others: list[Rule], tolerance: int) -> list[Rule]:
    return [o for o in others if any(c.overlaps(d, tolerance) for c in rule.citations for d in o.citations)]


@dataclass
class Pair:
    a: Rule
    b: Rule
    score: float
    reasons: list[str]

    @property
    def shared(self) -> list[str]:
        return sorted({str(c) for c in self.a.citations for d in self.b.citations if c.path == d.path and c.start <= d.end and d.start <= c.end})


def compare(rules_a: list[Rule], rules_b: list[Rule], threshold: float, tolerance: int) -> tuple[list[Rule], list[Rule], list[Pair], int]:
    """Return (only in A, only in B, differing pairs, agreeing pair count)."""
    only_a = [r for r in rules_a if not overlapping(r, rules_b, tolerance)]
    only_b = [r for r in rules_b if not overlapping(r, rules_a, tolerance)]

    # Each rule is paired with its most similar counterpart, so a rule that shares a
    # line with several rules of the other analyst is not reported against all of them.
    chosen: dict[tuple[int, int], float] = {}
    for side, others, flip in ((rules_a, rules_b, False), (rules_b, rules_a, True)):
        for r in side:
            candidates = overlapping(r, others, tolerance)
            if not candidates:
                continue
            best = max(candidates, key=lambda o: similarity(r.statement, o.statement))
            a, b = (best, r) if flip else (r, best)
            chosen[(id(a), id(b))] = similarity(a.statement, b.statement)

    by_id = {id(r): r for r in rules_a + rules_b}
    differing, agreeing = [], 0
    for (ia, ib), score in chosen.items():
        a, b = by_id[ia], by_id[ib]
        reasons = []
        if score < threshold:
            reasons.append(f"statement similarity {score:.2f} is below {threshold:.2f}")
        va, vb = values(a.statement), values(b.statement)
        if va != vb:
            reasons.append(f"values differ: A {sorted(va) or '{}'}, B {sorted(vb) or '{}'}")
        if negated(a.statement) != negated(b.statement):
            reasons.append("one statement is negated and the other is not")
        if reasons:
            differing.append(Pair(a, b, score, reasons))
        else:
            agreeing += 1
    differing.sort(key=lambda p: (p.a.file, p.a.id, p.b.id))
    return only_a, only_b, differing, agreeing


def md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def report(a_dir: Path, b_dir: Path, rules_a, rules_b, only_a, only_b, differing, agreeing, warnings) -> str:
    out = [
        "# Analysis comparison",
        "",
        f"- A: `{a_dir}` ({len(rules_a)} rules)",
        f"- B: `{b_dir}` ({len(rules_b)} rules)",
        f"- Counterpart pairs that agree: {agreeing}",
        f"- Counterpart pairs that may describe different behaviour: {len(differing)}",
        f"- Only in A: {len(only_a)}",
        f"- Only in B: {len(only_b)}",
        "",
    ]
    if warnings:
        out += ["## Warnings", "", *[f"- {w}" for w in warnings], ""]
    for title, rules in (("Only in A", only_a), ("Only in B", only_b)):
        out += [f"## {title}", ""]
        if not rules:
            out += ["None.", ""]
            continue
        out += ["| Rule | File | Category | Citations | Statement |", "|---|---|---|---|---|"]
        out += [f"| {r.id} | {r.file} | {r.category} | {', '.join(map(str, r.citations))} | {md_cell(r.statement)} |" for r in rules]
        out.append("")
    out += ["## Overlapping citations, possibly different behaviour", ""]
    if not differing:
        out += ["None.", ""]
    for p in differing:
        out += [
            f"### {p.a.label} / {p.b.label}",
            "",
            f"- Shared lines: {', '.join(p.shared) or 'adjacent lines only (within tolerance)'}",
            f"- Flagged because: {'; '.join(p.reasons)}",
        ]
        if p.a.category != p.b.category:
            out.append(f"- Categories: A `{p.a.category}`, B `{p.b.category}`")
        out += [f"- A ({p.a.file}): {p.a.statement}", f"- B ({p.b.file}): {p.b.statement}", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare analysis-a/rules with analysis-b/rules.")
    parser.add_argument("--a", type=Path, default=DEFAULT_A, help="Analyst A rules folder")
    parser.add_argument("--b", type=Path, default=DEFAULT_B, help="Analyst B rules folder")
    parser.add_argument("--threshold", type=float, default=0.6, help="flag pairs whose statement similarity is below this (default 0.6)")
    parser.add_argument("--line-tolerance", type=int, default=0, help="treat citations this many lines apart as overlapping (default 0)")
    parser.add_argument("--out", type=Path, help="write the Markdown report here instead of stdout")
    args = parser.parse_args(argv)

    for folder in (args.a, args.b):
        if not folder.is_dir():
            print(f"error: rules folder {folder} not found", file=sys.stderr)
            return 2

    rules_a, warn_a = load_rules(args.a, "A")
    rules_b, warn_b = load_rules(args.b, "B")
    only_a, only_b, differing, agreeing = compare(rules_a, rules_b, args.threshold, args.line_tolerance)
    text = report(args.a, args.b, rules_a, rules_b, only_a, only_b, differing, agreeing, warn_a + warn_b)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if only_a or only_b or differing else 0


if __name__ == "__main__":
    sys.exit(main())
