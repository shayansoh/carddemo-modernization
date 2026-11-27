from pathlib import Path

import yaml

import diff_analyses as da


def write(folder: Path, program: str, rules: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{program}.yaml").write_text(yaml.safe_dump({"program": program, "source": f"app/cbl/{program}.cbl", "rules": rules}))


def rule(rid: str, statement: str, *citations: str, category: str = "validation") -> dict:
    return {"id": rid, "statement": statement, "category": category, "module": "account", "citations": list(citations), "confidence": "high"}


def run(tmp_path: Path, a: list[dict], b: list[dict], *extra: str) -> tuple[int, str]:
    write(tmp_path / "a", "PROG1", a)
    write(tmp_path / "b", "PROG1", b)
    out = tmp_path / "report.md"
    code = da.main(["--a", str(tmp_path / "a"), "--b", str(tmp_path / "b"), "--out", str(out), *extra])
    return code, out.read_text()


def test_identical_analyses_agree(tmp_path):
    rules = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10-12")]
    code, text = run(tmp_path, rules, rules)
    assert code == 0
    assert "Counterpart pairs that agree: 1" in text


def test_rule_found_by_one_analyst(tmp_path):
    a = [
        rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10"),
        rule("PROG1-002", "A closed account cannot be updated.", "app/cbl/PROG1.cbl:50"),
    ]
    b = [rule("PROG1-007", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    code, text = run(tmp_path, a, b)
    assert code == 1
    only_a = text.split("## Only in A")[1].split("## Only in B")[0]
    assert "PROG1-002" in only_a and "PROG1-001" not in only_a
    assert "Only in B: 0" in text


def test_ids_do_not_matter_only_citations(tmp_path):
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:90")]
    code, text = run(tmp_path, a, b)
    assert code == 1
    assert "Only in A: 1" in text and "Only in B: 1" in text


def test_ranges_overlap(tmp_path):
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10-20")]
    b = [rule("PROG1-004", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:15")]
    code, _ = run(tmp_path, a, b)
    assert code == 0


def test_same_line_in_different_files_does_not_overlap(tmp_path):
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "The account number must be 11 digits.", "app/cpy/COPY1.cpy:10")]
    code, text = run(tmp_path, a, b)
    assert "Only in A: 1" in text


def test_line_tolerance(tmp_path):
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:12")]
    assert run(tmp_path, a, b)[0] == 1
    assert run(tmp_path, a, b, "--line-tolerance", "2")[0] == 0


def test_different_values_are_flagged(tmp_path):
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-003", "The account number must be 16 digits.", "app/cbl/PROG1.cbl:10")]
    code, text = run(tmp_path, a, b)
    assert code == 1
    assert "A PROG1-001 / B PROG1-003" in text
    assert "values differ" in text


def test_negation_is_flagged(tmp_path):
    a = [rule("PROG1-001", "A closed account can be updated by an administrator.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "A closed account cannot be updated by an administrator.", "app/cbl/PROG1.cbl:10")]
    code, text = run(tmp_path, a, b)
    assert code == 1 and "negated" in text


def test_unrelated_statements_are_flagged(tmp_path):
    a = [rule("PROG1-001", "The account number must be numeric.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "Pressing PF3 returns the user to the main menu.", "app/cbl/PROG1.cbl:10", category="navigation")]
    code, text = run(tmp_path, a, b)
    assert code == 1
    assert "similarity" in text and "Categories: A `validation`, B `navigation`" in text


def test_rephrasing_is_not_flagged(tmp_path):
    a = [rule("PROG1-001", "The account number must be numeric and must not be all zeros.", "app/cbl/PROG1.cbl:10")]
    b = [rule("PROG1-001", "The account number must be numeric and must not be all zeros on entry.", "app/cbl/PROG1.cbl:10")]
    assert run(tmp_path, a, b)[0] == 0


def test_best_counterpart_is_used(tmp_path):
    # A's rule shares line 10 with two B rules; only the closer one is its counterpart.
    a = [rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10")]
    b = [
        rule("PROG1-001", "The account number must be 11 digits.", "app/cbl/PROG1.cbl:10"),
        rule("PROG1-002", "An error message is shown in red.", "app/cbl/PROG1.cbl:10-11", category="presentation"),
    ]
    code, text = run(tmp_path, a, b)
    # B PROG1-002's best (only) counterpart is A PROG1-001, so that pair is still reported.
    assert "A PROG1-001 / B PROG1-002" in text
    assert "A PROG1-001 / B PROG1-001" not in text


def test_empty_folders(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert da.main(["--a", str(tmp_path / "a"), "--b", str(tmp_path / "b"), "--out", str(tmp_path / "r.md")]) == 0


def test_missing_folder_is_usage_error(tmp_path):
    assert da.main(["--a", str(tmp_path / "nope"), "--b", str(tmp_path)]) == 2
