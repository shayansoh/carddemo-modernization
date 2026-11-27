# diff_analyses

Compares Analyst A's rules (`docs/current-state/analysis-a/rules/`) with Analyst B's
(`docs/current-state/analysis-b/rules/`) and writes a Markdown report of:

- rules found by only one analyst
- rules whose citations overlap but whose statements may describe different behaviour

Rule IDs are local to each analyst, so rules are matched by citation: two rules are
counterparts when they cite overlapping lines of the same file. Each rule is compared with
its most similar counterpart. A pair is flagged when the statements are less similar than
`--threshold`, mention different numbers or quoted values, or one is negated and the other
is not. The check errs towards flagging; a person decides each flagged pair.

Run `tools/check_citations` on both folders first. This tool does not validate the schema.

## Run

From the repository root, once both analyses are finished:

```sh
uv run --project tools/diff_analyses tools/diff_analyses/diff_analyses.py --out /path/to/report.md
```

Options:

- `--a DIR`, `--b DIR`: rules folders (defaults above)
- `--threshold N`: similarity below which a pair is flagged, 0 to 1 (default 0.6)
- `--line-tolerance N`: treat citations up to N lines apart as overlapping (default 0)
- `--out FILE`: write the report to a file instead of stdout

Exit code: 0 no differences, 1 differences found, 2 a folder is missing.

Tests: `cd tools/diff_analyses && uv run --dev pytest -q`.
