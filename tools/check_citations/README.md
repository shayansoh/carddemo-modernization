# check_citations

Validates a folder of business-rule files (`<PROGRAM>.yaml`) against
`docs/current-state/rules/_schema.yaml` and checks every citation against the reference
clone. It fails (exit code 1) if:

- the clone is not at commit `59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e`, or has local changes
- a file is not valid YAML, does not match the schema, or its `program` differs from its file name
- a rule has no citation, or two rules in the folder share an ID
- a cited file is not in the clone at that commit
- a cited line, or the end of a cited range, is past the end of the file

Files whose names start with `_` are skipped. An empty folder passes.

Citations are `path:line` or `path:start-end`, relative to the clone root, for example
`app/cbl/COSGN00C.cbl:120` or `app/cpy/CVACT01Y.cpy:5-12`.

## Run

From the repository root:

```sh
uv run --project tools/check_citations tools/check_citations/check_citations.py \
  docs/current-state/analysis-a/rules ~/projects/reference/carddemo
```

Arguments: the rules folder, then the clone path. `--schema PATH` overrides the schema.
CI runs it on `docs/current-state/rules` and every `docs/current-state/analysis-*/rules`
folder that exists.

Tests: `cd tools/check_citations && uv run --dev pytest -q`.
