import subprocess
from pathlib import Path

import pytest

import check_citations as cc

GOOD_RULE = """\
program: PROG1
source: app/cbl/PROG1.cbl
rules:
  - id: PROG1-001
    statement: The account must exist before it can be shown.
    category: validation
    module: account
    citations:
      - app/cbl/PROG1.cbl:2
      - app/cbl/PROG1.cbl:1-3
    confidence: high
"""


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "clone"
    (repo / "app" / "cbl").mkdir(parents=True)
    (repo / "app" / "cbl" / "PROG1.cbl").write_text("line one\nline two\nline three\n")
    (repo / "app" / "cbl" / "NOEOL.cbl").write_bytes(b"a\r\nb")
    run_git(repo, "init", "-q")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    return repo, run_git(repo, "rev-parse", "HEAD")


def check(tmp_path: Path, clone: tuple[Path, str], files: dict[str, str], capsys) -> tuple[int, str]:
    rules = tmp_path / "rules"
    rules.mkdir(exist_ok=True)
    for name, text in files.items():
        (rules / name).write_text(text)
    code = cc.main([str(rules), str(clone[0]), "--expected-commit", clone[1]])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_empty_folder_passes(tmp_path, clone, capsys):
    code, out = check(tmp_path, clone, {}, capsys)
    assert code == 0 and "no rule files yet" in out


def test_schema_file_in_folder_is_skipped(tmp_path, clone, capsys):
    code, _ = check(tmp_path, clone, {"_schema.yaml": "not: a rule file"}, capsys)
    assert code == 0


def test_good_rules_pass(tmp_path, clone, capsys):
    code, out = check(tmp_path, clone, {"PROG1.yaml": GOOD_RULE}, capsys)
    assert code == 0, out
    assert "1 rules, 2 citations" in out


def test_last_line_without_newline_counts(tmp_path, clone, capsys):
    text = GOOD_RULE.replace("app/cbl/PROG1.cbl:1-3", "app/cbl/NOEOL.cbl:2")
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 0, out


def test_wrong_commit_fails(tmp_path, clone, capsys):
    rules = tmp_path / "rules"
    rules.mkdir()
    code = cc.main([str(rules), str(clone[0]), "--expected-commit", "0" * 40])
    assert code == 1
    assert "expected 0000" in capsys.readouterr().err


def test_dirty_clone_fails(tmp_path, clone, capsys):
    (clone[0] / "app" / "cbl" / "PROG1.cbl").write_text("changed\n")
    code, out = check(tmp_path, clone, {}, capsys)
    assert code == 1 and "local changes" in out


def test_missing_clone_fails(tmp_path, capsys):
    rules = tmp_path / "rules"
    rules.mkdir()
    assert cc.main([str(rules), str(tmp_path / "nowhere")]) == 1


@pytest.mark.parametrize(
    "citations, message",
    [
        ("[]", "no citation"),
        ("[app/cbl/MISSING.cbl:1]", "does not exist"),
        ("[app/cbl/PROG1.cbl:4]", "past the end"),
        ("[app/cbl/PROG1.cbl:2-9]", "past the end"),
        ("[app/cbl/PROG1.cbl:3-2]", "ends before it starts"),
        ("[app/cbl/PROG1.cbl]", "not path:line"),
        ("[app/cbl/PROG1.cbl:0]", "not path:line"),
        ("[../clone/app/cbl/PROG1.cbl:1]", "relative to the clone root"),
    ],
)
def test_bad_citations_fail(tmp_path, clone, capsys, citations, message):
    text = GOOD_RULE.replace(
        "    citations:\n      - app/cbl/PROG1.cbl:2\n      - app/cbl/PROG1.cbl:1-3\n",
        f"    citations: {citations}\n",
    )
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 1
    assert message in out


def test_missing_citations_key_fails(tmp_path, clone, capsys):
    text = GOOD_RULE.replace("    citations:\n      - app/cbl/PROG1.cbl:2\n      - app/cbl/PROG1.cbl:1-3\n", "")
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 1 and "no citation" in out


def test_schema_violations_fail(tmp_path, clone, capsys):
    text = GOOD_RULE.replace("confidence: high", "confidence: certain")
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 1 and "schema" in out


def test_low_confidence_needs_open_question(tmp_path, clone, capsys):
    text = GOOD_RULE.replace("confidence: high", "confidence: low")
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 1 and "open_question" in out
    text += "    open_question: Is this check also applied on update?\n"
    code, out = check(tmp_path, clone, {"PROG1.yaml": text}, capsys)
    assert code == 0, out


def test_program_must_match_file_name(tmp_path, clone, capsys):
    code, out = check(tmp_path, clone, {"OTHER.yaml": GOOD_RULE}, capsys)
    assert code == 1 and "does not match file name" in out


def test_duplicate_ids_fail(tmp_path, clone, capsys):
    second = GOOD_RULE.split("rules:\n", 1)[1]
    code, out = check(tmp_path, clone, {"PROG1.yaml": GOOD_RULE + second}, capsys)
    assert code == 1 and "duplicate rule id" in out


def test_invalid_yaml_fails(tmp_path, clone, capsys):
    code, out = check(tmp_path, clone, {"PROG1.yaml": "rules: [unclosed"}, capsys)
    assert code == 1 and "not valid YAML" in out
