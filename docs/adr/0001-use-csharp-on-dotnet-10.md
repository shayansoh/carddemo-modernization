---
status: accepted
date: 2026-11-25
decision-makers: Shayan
---

# Use C# on .NET 10 LTS for the rewrite

## Context and Problem Statement

The COBOL rewrite needs one language for the API, batch jobs and tests. Money results must match COBOL to the cent, and the runtime must stay supported for the life of the project.

## Considered Options

| Option | Money type | Support ends | Note |
| --- | --- | --- | --- |
| **C# / .NET 10 LTS** | `decimal` | Nov 2028 | Maintainer's core expertise; arithmetic written with operators |
| C# / .NET 11 STS | `decimal` | Nov 2028 | Same end date as .NET 10, less proven |
| Java / Spring Boot | `BigDecimal` | Per vendor | Most common modernization target; outside the maintainer's core expertise; arithmetic written as method calls |
| Python / FastAPI | `Decimal` | Per version | Easy to slip into floats; weaker fit for typed batch jobs |

## Decision Outcome

Chosen option: "C# / .NET 10 LTS", because it matches the maintainer's core expertise, which lowers delivery risk, and `decimal` arithmetic reads like the formulas in the specs, which makes reviews against COBOL easier. Java would serve equally well technically; .NET 11 adds no support time. SDK pinned in `global.json` (`10.0.115`, `rollForward: latestFeature`). Python is limited to tooling scripts.

### Consequences

* Good, because project effort goes into the migration itself rather than ramping up on a new ecosystem.
* Bad, because C# rounds where COBOL truncates, so every calculation must state its rounding.
* Bad, because Java is the more common modernization target.