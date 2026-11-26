# CardDemo Modernization

Rewrite of the AWS CardDemo COBOL application (CICS, VSAM, JCL batch) in C# on .NET 10, with behaviour proven equivalent to the original through automated tests.

## Repository layout

- `src/` - C# application code (API, batch, facade)
- `tests/` - equivalence, characterization and unit tests (locked after approval)
- `docs/specs/` - approved module specs (locked after approval)
- `docs/current-state/` - analysis of the legacy system, every claim cited
- `docs/adr/` - architecture decision records, MADR minimal format
- `tools/` - Python tooling scripts, run with `uv`
- Legacy source (read-only): `~/projects/reference/carddemo`, pinned to commit `59cc6c2`

## Commands

- Start the database: `docker compose up -d`, then wait for `docker compose ps` to show `(healthy)`
- Reset the database: `docker compose down -v` (deletes all data)
- Build: `dotnet build`
- Test: `dotnet test`

## Rules

1. Every claim about legacy behaviour cites the COBOL `file:line` it came from. No citation, no claim.
2. Implement from approved specs, never by translating COBOL line by line.
3. Never edit `docs/specs/` or `tests/` unless your role allows it. If a spec or test looks wrong, open an issue instead.
4. One task, one branch, one pull request, small enough to review in about 15 minutes.
5. When unsure, stop and write an open question rather than guess.
6. Never modify the reference clone.
7. Never push to `main` or merge a pull request. Push your branch, open a pull request with `gh pr create`, and stop for review.

## Conventions

- Money uses `decimal`, never `double` or `float`. Every calculation states its rounding explicitly; COBOL truncates unless the code says `ROUNDED`.
- Configuration, including connection strings, comes from environment variables or settings files, never from code.
- Never read, print or commit `.env`.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).
- Branch names: `<type>/<short-description>`, for example `feat/account-view-api`.
- A new architectural decision gets a new ADR in `docs/adr/`. Accepted ADRs are never edited; a later ADR supersedes them.