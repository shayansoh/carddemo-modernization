# inventory

Scans the base CardDemo application in the reference clone (everything under `app/`
except the optional `app/app-*` modules) and writes, in `docs/current-state/`:

- `inventory.csv`: one row per program (COBOL in `app/cbl`, assembler in `app/asm`, and
  programs defined in the CSD with no source)
- `inventory-evidence.csv`: every fact in `inventory.csv` with the `path:line` it came from
- `dependency-graph.md`: Mermaid diagrams of transactions and program flow, online data
  access, and batch jobs with their datasets

The generated files must not be edited by hand. CI regenerates them and fails if the
committed copies differ.

## Run

From the repository root:

```sh
uv run --project tools/inventory tools/inventory/inventory.py --clone ~/projects/reference/carddemo
```

Options: `--clone PATH` (default `~/projects/reference/carddemo`), `--out DIR` (default
`docs/current-state`). The script stops if the clone is not at commit
`59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e`.

Tests: `cd tools/inventory && uv run --dev pytest -q`. The last test scans the real clone
(`CARDDEMO_REF` or `~/projects/reference/carddemo`) and is skipped if it is not there.

## Columns

| Column | Source |
|---|---|
| `type` | `online` if the program has `EXEC CICS`; otherwise `batch`. A program that is only `CALL`ed takes its callers' type. |
| `transaction_ids` | `DEFINE TRANSACTION ... PROGRAM(...)` in `app/csd/CARDDEMO.CSD` |
| `bms_maps` | `MAPSET(MAP)` from `EXEC CICS SEND/RECEIVE MAP` |
| `copybooks` | `COPY` statements, including IBM-supplied `DFH*` copybooks |
| `files_accessed` | Online: CICS file or queue and the commands used on it. Batch: DD name from `SELECT ... ASSIGN` and the `OPEN` mode. |
| `datasets` | `name=DSN`. Online from `DEFINE FILE ... DSNAME` in the CSD; batch from the DD statements of the JCL steps that run the program (generation numbers dropped). |
| `programs_called` | `EXEC CICS XCTL`/`LINK` and `CALL` targets. Calls to the program itself are left out. |
| `jcl` | JCL and procedure members with an `EXEC PGM=` step for the program |
| `notes` | Anything the scanner could not resolve, or resolved indirectly |

## Limits

This is a pattern-based scanner, not a COBOL parser. It resolves names held in data items
from `VALUE` clauses and `MOVE` statements in the program and its copybooks, without
following control flow, so a value listed may be one the program never actually uses at
run time. When a target is also copied from a field with no `VALUE` (such as a COMMAREA
field), the row says so in `notes`. It is a map for analysts, not evidence of behaviour:
rule files must cite the COBOL source itself.
