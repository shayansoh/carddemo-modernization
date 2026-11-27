"""Generate the CardDemo program inventory and dependency graph.

Scans the base application of the reference clone (everything under ``app/``
except the optional ``app/app-*`` modules) and writes:

* ``inventory.csv``: one row per program
* ``inventory-evidence.csv``: one row per fact in ``inventory.csv``, with the
  ``path:line`` it was found at, so every entry can be checked against the source
* ``dependency-graph.md``: Mermaid diagrams of transactions, program calls,
  data access and batch jobs

This is a static, pattern-based scanner, not a COBOL compiler. It resolves names
held in variables (``EXEC CICS READ DATASET(LIT-ACCTFILENAME)``) from ``VALUE``
clauses and ``MOVE`` statements in the program and its copybooks. Anything it
cannot resolve is reported in the ``notes`` column rather than guessed.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PINNED_COMMIT = "59cc6c2fd7ebd7ef7925cad552a01a4b8b6e4d5e"

PROGRAM_DIR = "app/cbl"
ASM_DIR = "app/asm"
COPYBOOK_DIRS = ("app/cpy", "app/cpy-bms")
JCL_DIRS = ("app/jcl", "app/proc")
CSD_FILE = "app/csd/CARDDEMO.CSD"

CSV_COLUMNS = [
    "program",
    "source",
    "type",
    "transaction_ids",
    "bms_maps",
    "copybooks",
    "files_accessed",
    "datasets",
    "programs_called",
    "jcl",
    "notes",
]
EVIDENCE_COLUMNS = ["program", "kind", "value", "citation", "resolved_from"]

READ_OPS = {"READ", "STARTBR", "READNEXT", "READPREV", "ENDBR", "RESETBR", "READQ", "INPUT"}
WRITE_OPS = {"WRITE", "REWRITE", "DELETE", "WRITEQ", "OUTPUT", "EXTEND"}
BOTH_OPS = {"I-O"}
CICS_FILE_VERBS = {"READ", "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT", "READPREV", "ENDBR", "RESETBR", "UNLOCK"}

IDENT = r"[A-Z][A-Z0-9-]*"
NAME_VALUE = re.compile(r"[A-Z@#$][A-Z0-9@#$]{0,7}")

# Words that end a MOVE target list. Anything starting with END- is also a terminator.
RESERVED = {
    "ACCEPT", "ADD", "ALSO", "AND", "CALL", "CLOSE", "COMPUTE", "CONTINUE", "DELETE",
    "DISPLAY", "DIVIDE", "ELSE", "EVALUATE", "EXEC", "EXIT", "GO", "GOBACK", "IF",
    "INITIALIZE", "INSPECT", "MOVE", "MULTIPLY", "NEXT", "NOT", "OPEN", "OR", "PERFORM",
    "READ", "RETURN", "REWRITE", "SEARCH", "SET", "SORT", "START", "STOP", "STRING",
    "SUBTRACT", "THEN", "UNSTRING", "UNTIL", "VARYING", "WHEN", "WRITE", "TO", "FROM",
    "SPACE", "SPACES", "ZERO", "ZEROS", "ZEROES", "LOW-VALUE", "LOW-VALUES",
    "HIGH-VALUE", "HIGH-VALUES", "QUOTE", "QUOTES", "ALL", "FUNCTION", "LENGTH",
}


# --------------------------------------------------------------------------- sources


@dataclass
class Source:
    """One COBOL source file, reduced to code (columns 8-72, upper case).

    Comment lines become empty lines, so line numbers stay those of the file.
    """

    rel: str
    text: str
    starts: list[int]

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)

    def cite(self, offset: int) -> str:
        return f"{self.rel}:{self.line_of(offset)}"


def source_lines(path: Path) -> list[str]:
    """Lines split on LF only, as editors and tools/check_citations count them.

    ``str.splitlines`` would also split on form feeds and other control characters and
    shift line numbers.
    """
    text = path.read_bytes().decode("utf-8", errors="replace")
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_cobol(path: Path, rel: str) -> Source:
    codes = []
    for line in source_lines(path):
        if len(line) > 6 and line[6] in "*/":
            codes.append("")
            continue
        code = line[7:72]
        cut = code.find("*>")
        if cut >= 0:
            code = code[:cut]
        codes.append(code.upper())
    starts, pos = [], 0
    for code in codes:
        starts.append(pos)
        pos += len(code) + 1
    return Source(rel, "\n".join(codes), starts)


@dataclass
class Definition:
    name: str
    pic: str | None
    value: str | None
    cite: str


@dataclass
class Assignment:
    target: str
    literal: str | None  # set when the source is a literal
    variable: str | None  # set when the source is another data item
    cite: str


@dataclass
class Resolved:
    value: str
    cite: str  # where the literal value was found


def data_entries(src: Source):
    """Yield (offset, text) for each period-terminated entry or sentence."""
    pos = 0
    for m in re.finditer(r"\.(?=\s|$)", src.text):
        yield pos, src.text[pos : m.start()]
        pos = m.end()
    yield pos, src.text[pos:]


ENTRY_RE = re.compile(rf"^(\s*)(\d{{1,2}})\s+({IDENT}|FILLER)\b(.*)$", re.S)
PIC_RE = re.compile(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)")
VALUE_RE = re.compile(r"\bVALUES?\s+(?:IS\s+|ARE\s+)?(['\"])(.*?)\1", re.S)


def parse_definitions(src: Source) -> list[Definition]:
    defs = []
    for pos, entry in data_entries(src):
        m = ENTRY_RE.match(entry)
        if not m or m.group(2) == "88":
            continue
        rest = m.group(4)
        pic = PIC_RE.search(rest)
        value = VALUE_RE.search(rest)
        offset = pos + m.start(3)
        if value:
            offset = pos + m.start(4) + value.start(2)
        defs.append(
            Definition(
                name=m.group(3),
                pic=pic.group(1) if pic else None,
                value=value.group(2).strip() if value else None,
                cite=src.cite(offset),
            )
        )
    return defs


OPERAND = rf"({IDENT})(?:\s+(?:OF|IN)\s+{IDENT})*(?:\s*\([^)]*\))?"
MOVE_RE = re.compile(rf"\bMOVE\s+(?:(['\"])(.*?)\1|{OPERAND})\s+TO\b", re.S)
TARGET_RE = re.compile(rf"\s+{OPERAND}")


def parse_assignments(src: Source) -> list[Assignment]:
    out = []
    for m in MOVE_RE.finditer(src.text):
        literal = m.group(2).strip() if m.group(1) else None
        variable = m.group(3)
        if variable in RESERVED:
            continue
        pos = m.end()
        while True:
            t = TARGET_RE.match(src.text, pos)
            if not t or t.group(1) in RESERVED or t.group(1).startswith("END-"):
                break
            out.append(Assignment(t.group(1), literal, variable, src.cite(m.start())))
            pos = t.end()
    return out


@dataclass
class SymbolTable:
    """Data names visible to one program: its own source plus its copybooks."""

    defs: dict[str, list[Definition]] = field(default_factory=lambda: defaultdict(list))
    assigns: dict[str, list[Assignment]] = field(default_factory=lambda: defaultdict(list))
    by_file: dict[str, list[Definition]] = field(default_factory=lambda: defaultdict(list))

    def add(self, src: Source) -> None:
        for d in parse_definitions(src):
            self.defs[d.name].append(d)
            self.by_file[src.rel].append(d)
        for a in parse_assignments(src):
            self.assigns[a.target].append(a)

    def resolve(self, name: str) -> tuple[list[Resolved], set[str], set[str]]:
        """Return (values, data names with no static value, run-time data names copied into ``name``)."""
        values: dict[str, Resolved] = {}
        unresolved: set[str] = set()
        via: set[str] = set()
        seen: set[str] = set()

        def walk(n: str, depth: int) -> None:
            if n in seen:
                return
            seen.add(n)
            found = False
            for d in self.defs.get(n, []):
                if d.value:
                    values.setdefault(d.value, Resolved(d.value, d.cite))
                    found = True
            for a in self.assigns.get(n, []):
                found = True
                if a.literal is not None:
                    values.setdefault(a.literal, Resolved(a.literal, a.cite))
                elif a.variable:
                    # A source with no VALUE clause of its own (for example a COMMAREA
                    # field) is filled at run time, so the static values may be incomplete.
                    if depth == 0 and not any(d.value for d in self.defs.get(a.variable, [])):
                        via.add(a.variable)
                    walk(a.variable, depth + 1)
            if not found:
                unresolved.add(n)

        walk(name, 0)
        return list(values.values()), unresolved, via

    def table_values(self, name: str) -> list[Resolved]:
        """Fallback for OCCURS tables filled by FILLER VALUEs (for example menu option tables).

        Returns the name-like literals, in the copybook that defines ``name``, whose PIC
        matches the PIC of ``name``.
        """
        out = []
        for d in self.defs.get(name, []):
            if not d.pic:
                continue
            rel = d.cite.rsplit(":", 1)[0]
            for other in self.by_file[rel]:
                if other.pic == d.pic and other.value and NAME_VALUE.fullmatch(other.value):
                    out.append(Resolved(other.value, other.cite))
        return out


# --------------------------------------------------------------------------- CICS and batch statements

EXEC_CICS_RE = re.compile(r"\bEXEC\s+CICS\b(.*?)\bEND-EXEC\b", re.S)
COPY_RE = re.compile(r"\bCOPY\s+['\"]?([A-Z0-9@#$-]+)['\"]?")
SELECT_RE = re.compile(rf"\bSELECT\s+(?:OPTIONAL\s+)?({IDENT})\s+ASSIGN\s+(?:TO\s+)?['\"]?([A-Z0-9-]+)")
OPEN_RE = re.compile(r"\bOPEN\b")
CALL_RE = re.compile(rf"\bCALL\s+(?:(['\"])([A-Z0-9@#$-]+)\1|({IDENT}))")
USING_RE = re.compile(r"\bPROCEDURE\s+DIVISION\s+USING\b")


def cics_option(body: str, option: str) -> str | None:
    """Return the argument of OPTION(...) in an EXEC CICS body, allowing nested parens."""
    m = re.search(rf"\b{option}\s*\(", body)
    if not m:
        return None
    depth, start = 1, m.end()
    for i in range(start, len(body)):
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if depth == 0:
                return body[start:i].strip()
    return None


def operand_name(arg: str) -> tuple[str | None, str | None]:
    """Split an operand into (literal, data-name). Subscripts and qualifiers are dropped."""
    arg = arg.strip()
    m = re.fullmatch(r"(['\"])(.*)\1", arg, re.S)
    if m:
        return m.group(2).strip(), None
    m = re.match(IDENT, arg)
    return None, (m.group(0) if m else None)


def strip_ddname(name: str) -> str:
    """ASSIGN TO UT-S-ACCTFILE names DD ACCTFILE."""
    return re.sub(r"^(?:(?:UT|DA)-)?[SD]-|^AS-", "", name)


# --------------------------------------------------------------------------- CSD and JCL


@dataclass
class CsdInfo:
    transactions: dict[str, list[tuple[str, str]]]  # program -> [(tranid, cite)]
    file_dsn: dict[str, tuple[str, str]]  # CICS file -> (dsn, cite)
    programs: dict[str, str]  # program -> cite of DEFINE PROGRAM


def parse_csd(path: Path, rel: str) -> CsdInfo:
    lines = source_lines(path)
    blocks: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("DEFINE "):
            blocks.append((i, line))
        elif blocks:
            blocks[-1] = (blocks[-1][0], blocks[-1][1] + " " + line)
    info = CsdInfo(defaultdict(list), {}, {})
    for lineno, text in blocks:
        m = re.match(r"\s*DEFINE\s+(\w+)\((\w+)\)", text)
        if not m:
            continue
        kind, name = m.groups()
        cite = f"{rel}:{lineno}"
        if kind == "TRANSACTION":
            p = re.search(r"\bPROGRAM\((\w+)\)", text)
            if p:
                info.transactions[p.group(1)].append((name, cite))
        elif kind == "FILE":
            d = re.search(r"\bDSNAME\(([^)]+)\)", text)
            if d:
                info.file_dsn[name] = (d.group(1).strip(), cite)
        elif kind == "PROGRAM":
            info.programs[name] = cite
    return info


@dataclass
class JclStep:
    file: str
    program: str
    cite: str
    dds: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))


def normalise_dsn(dsn: str) -> str:
    """Drop generation numbers and members: AWS.X.DALYREJS(+1) -> AWS.X.DALYREJS."""
    return re.sub(r"\(.*\)$", "", dsn)


def parse_jcl(path: Path, rel: str) -> list[JclStep]:
    """Return the EXEC PGM= steps in a JCL or procedure file, with their DD datasets."""
    statements: list[tuple[int, str, str, str]] = []  # (line, name, operation, operands)
    for i, line in enumerate(source_lines(path), 1):
        if not line.startswith("//") or line.startswith("//*"):
            continue
        body = line[2:71]
        m = re.match(r"(\S*)\s+(\S+)\s*(.*)", body)
        if not m:
            continue
        name, op, operands = m.groups()
        if name == "" and statements and op not in {"EXEC", "DD", "PEND", "JOB", "PROC"}:
            # continuation line: "//      DSN=..."
            ln, n, o, prev = statements[-1]
            statements[-1] = (ln, n, o, prev + "," + (op + " " + operands).strip())
            continue
        statements.append((i, name, op, operands))

    steps: list[JclStep] = []
    current: JclStep | None = None
    for lineno, name, op, operands in statements:
        if op == "EXEC":
            p = re.search(r"\bPGM=([A-Z0-9@#$]+)", operands)
            current = JclStep(rel, p.group(1), f"{rel}:{lineno}") if p else None
            if current:
                steps.append(current)
        elif op == "DD" and current is not None and name and "." not in name:
            d = re.search(r"\bDSN(?:AME)?=([^,\s]+)", operands)
            if d:
                current.dds[name].append((normalise_dsn(d.group(1)), f"{rel}:{lineno}"))
        elif op in {"JOB", "PROC", "PEND"}:
            current = None
    return steps


# --------------------------------------------------------------------------- per-program scan


@dataclass
class Program:
    name: str
    source: str
    type: str
    transactions: list[str] = field(default_factory=list)
    maps: list[str] = field(default_factory=list)
    copybooks: list[str] = field(default_factory=list)
    files: dict[str, set[str]] = field(default_factory=dict)  # name -> ops
    datasets: list[tuple[str, str]] = field(default_factory=list)  # (logical name, dsn)
    calls: dict[str, set[str]] = field(default_factory=dict)  # program -> {XCTL, LINK, CALL}
    jcl: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    has_using: bool = False
    evidence: list[tuple[str, str, str, str]] = field(default_factory=list)

    def add_evidence(self, kind: str, value: str, cite: str, resolved_from: str = "") -> None:
        row = (kind, value, cite, resolved_from)
        if row not in self.evidence:
            self.evidence.append(row)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def find_copybook(clone: Path, name: str) -> Path | None:
    for d in COPYBOOK_DIRS:
        for p in sorted((clone / d).glob("*")):
            if p.stem.upper() == name.upper():
                return p
    return None


class Scanner:
    def __init__(self, clone: Path):
        self.clone = clone
        self.csd = parse_csd(clone / CSD_FILE, CSD_FILE)
        self.steps: list[JclStep] = []
        for d in JCL_DIRS:
            for p in sorted((clone / d).iterdir()):
                if p.is_file():
                    self.steps.extend(parse_jcl(p, f"{d}/{p.name}"))
        self.copybook_cache: dict[str, Source | None] = {}

    def copybook(self, name: str) -> Source | None:
        if name not in self.copybook_cache:
            p = find_copybook(self.clone, name)
            self.copybook_cache[name] = read_cobol(p, p.relative_to(self.clone).as_posix()) if p else None
        return self.copybook_cache[name]

    def resolve(self, prog: Program, table: SymbolTable, arg: str, what: str, use_cite: str) -> list[Resolved]:
        literal, name = operand_name(arg)
        if literal is not None:
            return [Resolved(literal, use_cite)]
        if name is None:
            prog.note(f"{what} operand '{arg}' not understood at {use_cite}")
            return []
        values, unresolved, via = table.resolve(name)
        if not values:
            values = table.table_values(name)
            if values:
                unresolved.discard(name)
                where = sorted({v.cite.rsplit(':', 1)[0] for v in values})
                prog.note(f"{what} {name} is a table entry; values taken from same-PIC literals in {', '.join(where)}")
        if unresolved and values:
            prog.note(f"{what} {name}: partly static; {', '.join(sorted(unresolved))} has no static value")
        elif not values:
            prog.note(f"{what} {name} not resolved statically at {use_cite}")
        if via:
            prog.note(f"{what} {name} is also copied from {', '.join(sorted(via))} at run time; static values may be incomplete")
        return values

    def scan_program(self, path: Path) -> Program:
        rel = path.relative_to(self.clone).as_posix()
        src = read_cobol(path, rel)
        name = path.stem.upper()
        is_online = bool(EXEC_CICS_RE.search(src.text))
        prog = Program(name=name, source=rel, type="online" if is_online else "batch")

        table = SymbolTable()
        table.add(src)
        for m in COPY_RE.finditer(src.text):
            cb = m.group(1)
            if cb == "REPLACING":
                continue
            if cb not in prog.copybooks:
                prog.copybooks.append(cb)
            prog.add_evidence("copybook", cb, src.cite(m.start()))
            cb_src = self.copybook(cb)
            if cb_src is not None:
                table.add(cb_src)
            elif not cb.startswith("DFH"):
                prog.note(f"copybook {cb} not found in {', '.join(COPYBOOK_DIRS)}")

        for tranid, cite in self.csd.transactions.get(name, []):
            prog.transactions.append(tranid)
            prog.add_evidence("transaction", tranid, cite)

        self.scan_cics(prog, src, table)
        self.scan_batch_files(prog, src)
        self.scan_calls(prog, src, table)
        prog.has_using = bool(USING_RE.search(src.text))
        return prog

    def scan_cics(self, prog: Program, src: Source, table: SymbolTable) -> None:
        for m in EXEC_CICS_RE.finditer(src.text):
            body = m.group(1)
            cite = src.cite(m.start())
            verb = body.split()[0] if body.split() else ""
            if verb in CICS_FILE_VERBS:
                arg = cics_option(body, "FILE") or cics_option(body, "DATASET")
                if arg:
                    for v in self.resolve(prog, table, arg, f"{verb} file", cite):
                        prog.files.setdefault(v.value, set()).add(verb)
                        prog.add_evidence("file", f"{v.value}:{verb}", cite, v.cite if v.cite != cite else "")
            elif verb in {"XCTL", "LINK"}:
                arg = cics_option(body, "PROGRAM")
                if arg:
                    for v in self.resolve(prog, table, arg, f"{verb} target", cite):
                        if v.value == prog.name:
                            continue
                        prog.calls.setdefault(v.value, set()).add(verb)
                        prog.add_evidence("program_call", f"{v.value}:{verb}", cite, v.cite if v.cite != cite else "")
            elif verb in {"WRITEQ", "READQ", "DELETEQ"}:
                arg = cics_option(body, "QUEUE")
                kind = "TDQ" if re.search(r"\bTD\b", body) else "TSQ"
                if arg:
                    for v in self.resolve(prog, table, arg, f"{verb} queue", cite):
                        key = f"{v.value}({kind})"
                        prog.files.setdefault(key, set()).add(verb)
                        prog.add_evidence("file", f"{key}:{verb}", cite, v.cite if v.cite != cite else "")
            if verb in {"SEND", "RECEIVE"} and cics_option(body, "MAP"):
                maps = self.resolve(prog, table, cics_option(body, "MAP"), f"{verb} MAP", cite)
                mapset_arg = cics_option(body, "MAPSET")
                mapsets = self.resolve(prog, table, mapset_arg, f"{verb} MAPSET", cite) if mapset_arg else maps
                for ms in mapsets:
                    for mp in maps:
                        entry = f"{ms.value}({mp.value})"
                        if entry not in prog.maps:
                            prog.maps.append(entry)
                        prog.add_evidence("bms_map", entry, cite, "; ".join(sorted({c for c in (ms.cite, mp.cite) if c != cite})))

    def scan_batch_files(self, prog: Program, src: Source) -> None:
        selects: dict[str, str] = {}
        for m in SELECT_RE.finditer(src.text):
            dd = strip_ddname(m.group(2))
            selects[m.group(1)] = dd
            prog.files.setdefault(dd, set())
            prog.add_evidence("file", dd, src.cite(m.start()))
        for m in OPEN_RE.finditer(src.text):
            mode = None
            for tok in src.text[m.end() :].split():
                tok = tok.rstrip(".")
                if tok in {"INPUT", "OUTPUT", "I-O", "EXTEND"}:
                    mode = tok
                elif tok in selects and mode:
                    prog.files[selects[tok]].add(mode)
                    prog.add_evidence("file", f"{selects[tok]}:{mode}", src.cite(m.start()))
                else:
                    break

    def scan_calls(self, prog: Program, src: Source, table: SymbolTable) -> None:
        for m in CALL_RE.finditer(src.text):
            cite = src.cite(m.start())
            arg = f"'{m.group(2)}'" if m.group(1) else m.group(3)
            for v in self.resolve(prog, table, arg, "CALL target", cite):
                prog.calls.setdefault(v.value, set()).add("CALL")
                prog.add_evidence("program_call", f"{v.value}:CALL", cite, v.cite if v.cite != cite else "")

    def link(self, programs: dict[str, Program]) -> None:
        """Cross-program facts: types of called subprograms, JCL steps and datasets."""
        callers: dict[str, set[str]] = defaultdict(set)
        for p in programs.values():
            for callee, how in p.calls.items():
                if "CALL" in how:
                    callers[callee].add(p.name)
            missing = sorted(c for c in p.calls if c not in programs and not c.startswith("CEE"))
            if missing:
                p.note(f"calls programs with no source in the base application: {', '.join(missing)}")

        # A program without EXEC CICS that is only CALLed runs wherever its callers run.
        for p in programs.values():
            own_steps = [s for s in self.steps if s.program == p.name]
            if own_steps or p.type != "batch" or not callers.get(p.name):
                continue
            caller_types = {programs[c].type for c in callers[p.name] if c in programs}
            if caller_types:
                p.type = "; ".join(sorted(caller_types))
            p.note(f"called by {', '.join(sorted(callers[p.name]))}; type taken from its callers")

        for p in programs.values():
            if not p.source.startswith(PROGRAM_DIR):
                continue
            own_steps = [s for s in self.steps if s.program == p.name]
            for s in own_steps:
                if s.file not in p.jcl:
                    p.jcl.append(s.file)
                p.add_evidence("jcl", s.file, s.cite)
            if "online" in p.type:
                for f in sorted(p.files):
                    if f in self.csd.file_dsn:
                        dsn, cite = self.csd.file_dsn[f]
                        p.datasets.append((f, dsn))
                        p.add_evidence("dataset", f"{f}={dsn}", cite)
                    elif not f.endswith("Q)"):
                        p.note(f"CICS file {f} has no DEFINE FILE in {CSD_FILE}")
                continue
            steps = own_steps or [s for s in self.steps if s.program in callers.get(p.name, set())]
            if not p.files:
                steps = steps if own_steps else []
            if not own_steps and steps:
                p.note(f"no JCL step runs it; datasets taken from steps of {', '.join(sorted({s.program for s in steps}))}")
            for f in sorted(p.files):
                for s in steps:
                    for dsn, cite in s.dds.get(f, []):
                        if (f, dsn) not in p.datasets:
                            p.datasets.append((f, dsn))
                        p.add_evidence("dataset", f"{f}={dsn}", cite)
            if not steps:
                p.note(f"not run by any EXEC PGM= step in {', '.join(JCL_DIRS)}")
            if p.has_using and not own_steps:
                p.note("subprogram (PROCEDURE DIVISION USING)")

    def scan(self) -> dict[str, Program]:
        programs: dict[str, Program] = {}
        for path in sorted((self.clone / PROGRAM_DIR).iterdir(), key=lambda p: p.name.upper()):
            if path.suffix.lower() == ".cbl":
                prog = self.scan_program(path)
                programs[prog.name] = prog
        for path in sorted((self.clone / ASM_DIR).iterdir()):
            if path.suffix.lower() == ".asm":
                name = path.stem.upper()
                prog = Program(name, path.relative_to(self.clone).as_posix(), "batch")
                prog.note("assembler source; not scanned")
                programs[name] = prog
        for name, cite in sorted(self.csd.programs.items()):
            if name not in programs:
                prog = Program(name, "", "online")
                prog.note(f"defined in {cite} but no source in {PROGRAM_DIR}")
                for tranid, tcite in self.csd.transactions.get(name, []):
                    prog.transactions.append(tranid)
                    prog.add_evidence("transaction", tranid, tcite)
                prog.add_evidence("csd_program", name, cite)
                programs[name] = prog
        self.link(programs)
        return programs


# --------------------------------------------------------------------------- output


def join(items) -> str:
    return "; ".join(items)


def ops_text(ops: set[str]) -> str:
    return "+".join(sorted(ops))


def inventory_rows(programs: dict[str, Program]) -> list[dict[str, str]]:
    rows = []
    for name in sorted(programs):
        p = programs[name]
        rows.append(
            {
                "program": p.name,
                "source": p.source,
                "type": p.type,
                "transaction_ids": join(sorted(p.transactions)),
                "bms_maps": join(sorted(p.maps)),
                "copybooks": join(sorted(p.copybooks)),
                "files_accessed": join(f"{f}:{ops_text(o)}" if o else f for f, o in sorted(p.files.items())),
                "datasets": join(f"{f}={d}" for f, d in sorted(p.datasets)),
                "programs_called": join(f"{c}:{ops_text(h)}" for c, h in sorted(p.calls.items())),
                "jcl": join(sorted(p.jcl)),
                "notes": join(p.notes),
            }
        )
    return rows


def evidence_rows(programs: dict[str, Program]) -> list[dict[str, str]]:
    rows = []
    for name in sorted(programs):
        for kind, value, cite, resolved in programs[name].evidence:
            rows.append({"program": name, "kind": kind, "value": value, "citation": cite, "resolved_from": resolved})
    return rows


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


def node_id(kind: str, name: str) -> str:
    return f"{kind}_{re.sub(r'[^A-Za-z0-9]', '_', name)}"


def short_dsn(dsn: str) -> str:
    return dsn.removeprefix("AWS.M2.CARDDEMO.")


def direction(ops: set[str]) -> str:
    reads = bool(ops & (READ_OPS | BOTH_OPS))
    writes = bool(ops & (WRITE_OPS | BOTH_OPS))
    if reads and writes:
        return "both"
    if writes:
        return "write"
    return "read"


def data_edge(prog: str, store: str, ops: set[str]) -> str:
    d = direction(ops)
    if d == "both":
        return f"    {store} <--> {prog}"
    if d == "write":
        return f"    {prog} --> {store}"
    return f"    {store} --> {prog}"


def mermaid(programs: dict[str, Program]) -> str:
    online = [p for n, p in sorted(programs.items()) if "online" in p.type]
    batch = [p for n, p in sorted(programs.items()) if "batch" in p.type]
    ignored_calls = re.compile(r"^CEE")

    def prog_node(name: str) -> str:
        label = name if name in programs and programs[name].source else f"{name} (no source)"
        return f'    {node_id("P", name)}["{label}"]'

    out = []
    # 1. transactions and online program flow
    lines = ["flowchart LR"]
    nodes: set[str] = set()
    edges: list[str] = []
    for p in online:
        nodes.add(p.name)
        for t in sorted(p.transactions):
            lines.append(f'    {node_id("T", t)}(["{t}"])')
            edges.append(f'    {node_id("T", t)} --> {node_id("P", p.name)}')
        for callee, how in sorted(p.calls.items()):
            if ignored_calls.match(callee):
                continue
            nodes.add(callee)
            edges.append(f'    {node_id("P", p.name)} -->|{ops_text(how)}| {node_id("P", callee)}')
    lines += [prog_node(n) for n in sorted(nodes)] + edges
    out.append(("Online: transactions and program flow", "Rounded nodes are CICS transaction IDs (from the CSD). Edges are `EXEC CICS XCTL`/`LINK` and `CALL` targets.", lines))

    # 2. online data access
    lines = ["flowchart LR"]
    stores: dict[str, str] = {}
    edges = []
    for p in online:
        dsns = dict(p.datasets)
        for f, ops in sorted(p.files.items()):
            label = f"{f}<br/>{short_dsn(dsns[f])}" if f in dsns else f
            sid = node_id("D", dsns.get(f, f))
            stores.setdefault(sid, label)
            edges.append(data_edge(node_id("P", p.name), sid, ops))
    lines += [prog_node(p.name) for p in online if p.files]
    lines += [f'    {sid}[("{label}")]' for sid, label in sorted(stores.items())] + edges
    out.append(("Online: data access", "Cylinders are CICS files (with dataset) and queues. Arrows point from a store a program reads to the program, and from a program to a store it writes; `<-->` is both.", lines))

    # 3. batch jobs, programs and datasets
    lines = ["flowchart LR"]
    stores = {}
    edges = []
    jobs: set[str] = set()
    for p in batch:
        for j in sorted(p.jcl):
            jobs.add(j)
            edges.append(f'    {node_id("J", j)} --> {node_id("P", p.name)}')
        for callee, how in sorted(p.calls.items()):
            if ignored_calls.match(callee):
                continue
            edges.append(f'    {node_id("P", p.name)} -->|{ops_text(how)}| {node_id("P", callee)}')
        by_dd: dict[str, list[str]] = defaultdict(list)
        for f, d in p.datasets:
            by_dd[f].append(d)
        for f, ops in sorted(p.files.items()):
            for d in by_dd.get(f, [f"DD {f}"]):
                sid = node_id("D", d)
                stores.setdefault(sid, short_dsn(d))
                edges.append(data_edge(node_id("P", p.name), sid, ops))
    lines += [f'    {node_id("J", j)}{{{{"{j}"}}}}' for j in sorted(jobs)]
    lines += [prog_node(p.name) for p in batch]
    lines += [f'    {sid}[("{label}")]' for sid, label in sorted(stores.items())] + edges
    out.append(("Batch: jobs, programs and datasets", "Hexagons are JCL or procedure members with an `EXEC PGM=` step for the program. Cylinders are datasets named on the step's DD statements (generation numbers dropped). Arrow direction follows the `OPEN` mode.", lines))

    md = [
        "# CardDemo dependency graph",
        "",
        f"Generated by `tools/inventory` from the reference clone at commit `{PINNED_COMMIT}`. Do not edit by hand.",
        "",
        "Scope: the base application under `app/` (the optional `app/app-*` modules are excluded).",
        "Every node and edge is listed with its source `path:line` in `inventory-evidence.csv`.",
        "Language Environment services (`CEE*`) are left out of the diagrams.",
        "",
    ]
    for title, caption, body in out:
        md += [f"## {title}", "", caption, "", "```mermaid", *body, "```", ""]
    return "\n".join(md)


# --------------------------------------------------------------------------- main


def clone_commit(clone: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clone", type=Path, default=Path("~/projects/reference/carddemo").expanduser(), help="path to the reference clone")
    parser.add_argument("--out", type=Path, default=Path("docs/current-state"), help="output folder")
    args = parser.parse_args(argv)

    clone = args.clone.expanduser().resolve()
    commit = clone_commit(clone)
    if commit != PINNED_COMMIT:
        print(f"error: {clone} is at {commit}, expected {PINNED_COMMIT}", file=sys.stderr)
        return 1

    programs = Scanner(clone).scan()
    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "inventory.csv", CSV_COLUMNS, inventory_rows(programs))
    write_csv(args.out / "inventory-evidence.csv", EVIDENCE_COLUMNS, evidence_rows(programs))
    (args.out / "dependency-graph.md").write_text(mermaid(programs), encoding="utf-8")
    print(f"wrote {len(programs)} programs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
