---
status: accepted
date: 2026-11-26
decision-makers: Shayan
---

# Record legacy business rules as cited, schema-checked YAML

## Context and Problem Statement

M1 discovery extracts business rules from the COBOL source using two analyst agents working independently. The rules later drive module boundaries, slice order and behaviour tests, so each one must be traceable to the code, checkable without reading COBOL, and comparable between the two analyses.

## Considered Options

| Option | Citations checked | A/B comparison | Review effort | Note |
| --- | --- | --- | --- | --- |
| **YAML rule files with a JSON Schema** | Automatically, in CI | Tool-assisted | Low per rule | One file per program; structured fields |
| Markdown analysis per program | Manually | Manual reading | High | Original analyst output; easy to read, hard to verify |
| One CSV of all rules | Automatically | Tool-assisted | Low per rule | Poor fit for multi-line statements and lists of citations |

## Decision Outcome

Chosen option: "YAML rule files with a JSON Schema", because every citation can be checked against the reference clone in CI, and structured fields make the two analyses comparable without reading COBOL.

* One file per program: `analysis-a/rules/`, `analysis-b/rules/`, then `rules/` once reconciled.
* Citations are `path:line` or `path:start-end` at the pinned reference commit `59cc6c2`. `tools/check_citations` fails on a missing file, a line past the end of the file, a dirty or wrong clone, a schema error or a duplicate ID.
* Rule IDs are local to each analyst. `tools/diff_analyses` pairs A and B rules by overlapping citations and flags rules found by one analyst only, or pairs that may differ. The owner decides every flagged item.
* Each rule has a `category` from a fixed list and a `confidence` of high, medium or low. A rule that fits no category uses `other`. Low confidence or category `other` requires an `open_question`.
* Tooling is in Python with `uv`, consistent with ADR 0001.

### Consequences

* Good, because "no citation, no claim" is enforced by CI rather than by review.
* Good, because the inventory is generated with its own path:line evidence, so it follows the same rule.
* Bad, because pairing by citation misses rules where the two analysts cite different lines for the same behaviour; these show up as one-analyst findings and need a manual check.
* Bad, because `other` can become a catch-all; rules in it are reviewed at gate G1 and the list is extended where they form a pattern.
* Bad, because a change to the pinned reference commit invalidates every citation.