import os
from pathlib import Path

import pytest

import inventory as inv


def src(tmp_path: Path, text: str, name: str = "PROG1.cbl") -> inv.Source:
    p = tmp_path / name
    p.write_text(text)
    return inv.read_cobol(p, f"app/cbl/{name}")


def test_comments_and_sequence_columns_are_ignored(tmp_path):
    s = src(tmp_path, "000100 01 A PIC X(8) VALUE 'X'.                                   SEQ00001\n"
                      "000200*01 B PIC X(8) VALUE 'Y'.\n")
    assert "SEQ00001" not in s.text
    assert [d.name for d in inv.parse_definitions(s)] == ["A"]


def test_value_on_continuation_line_is_found_with_its_line(tmp_path):
    s = src(tmp_path, "       01 WS-LITERALS.\n"
                      "          05 LIT-ACCTFILENAME        PIC X(8)\n"
                      "                                     VALUE 'ACCTDAT '.\n")
    d = {d.name: d for d in inv.parse_definitions(s)}["LIT-ACCTFILENAME"]
    assert d.value == "ACCTDAT" and d.pic == "X(8)" and d.cite == "app/cbl/PROG1.cbl:3"


def test_move_chains_resolve_and_runtime_sources_are_reported(tmp_path):
    s = src(tmp_path, "       01 LIT-MENU PIC X(8) VALUE 'COMEN01C'.\n"
                      "       01 TO-PGM   PIC X(8).\n"
                      "       01 FROM-PGM PIC X(8).\n"
                      "           MOVE LIT-MENU TO TO-PGM\n"
                      "           MOVE 'COSGN00C' TO TO-PGM FROM-PGM\n"
                      "           MOVE FROM-PGM TO TO-PGM\n")
    t = inv.SymbolTable()
    t.add(s)
    values, unresolved, via = t.resolve("TO-PGM")
    assert sorted(v.value for v in values) == ["COMEN01C", "COSGN00C"]
    assert via == {"FROM-PGM"}  # FROM-PGM has no VALUE: filled at run time
    assert unresolved == set()


def test_table_fallback_uses_same_pic_literals(tmp_path):
    s = src(tmp_path, "       01 OPTIONS.\n"
                      "          05 FILLER PIC X(35) VALUE 'Account View'.\n"
                      "          05 FILLER PIC X(08) VALUE 'COACTVWC'.\n"
                      "          05 FILLER PIC X(01) VALUE 'U'.\n"
                      "       01 OPTIONS-R REDEFINES OPTIONS.\n"
                      "          05 OPT OCCURS 1 TIMES.\n"
                      "             10 OPT-NAME PIC X(35).\n"
                      "             10 OPT-PGM  PIC X(08).\n"
                      "             10 OPT-TYPE PIC X(01).\n", "MENU.cpy")
    t = inv.SymbolTable()
    t.add(s)
    assert t.resolve("OPT-PGM")[0] == []
    assert [v.value for v in t.table_values("OPT-PGM")] == ["COACTVWC"]


def test_cics_option_handles_subscripts():
    body = " XCTL PROGRAM(CDEMO-MENU-OPT-PGMNAME(WS-OPTION))\n COMMAREA(CARDDEMO-COMMAREA)\n"
    assert inv.cics_option(body, "PROGRAM") == "CDEMO-MENU-OPT-PGMNAME(WS-OPTION)"
    assert inv.operand_name("CDEMO-MENU-OPT-PGMNAME(WS-OPTION)") == (None, "CDEMO-MENU-OPT-PGMNAME")
    assert inv.operand_name("'COSGN0A'") == ("COSGN0A", None)


def test_jcl_steps_and_datasets(tmp_path):
    p = tmp_path / "POST.jcl"
    p.write_text("//POST JOB 'X'\n"
                 "//* comment DSN=IGNORED\n"
                 "//STEP15 EXEC PGM=CBTRN02C\n"
                 "//TRANFILE DD DISP=SHR,\n"
                 "//         DSN=AWS.M2.CARDDEMO.TRANSACT.VSAM.KSDS\n"
                 "//DALYREJS DD DISP=(NEW,CATLG,DELETE),\n"
                 "//         DSN=AWS.M2.CARDDEMO.DALYREJS(+1)\n"
                 "//STEP20 EXEC PGM=IEFBR14\n")
    steps = inv.parse_jcl(p, "app/jcl/POST.jcl")
    assert [s.program for s in steps] == ["CBTRN02C", "IEFBR14"]
    assert steps[0].cite == "app/jcl/POST.jcl:3"
    assert dict(steps[0].dds) == {
        "TRANFILE": [("AWS.M2.CARDDEMO.TRANSACT.VSAM.KSDS", "app/jcl/POST.jcl:4")],
        "DALYREJS": [("AWS.M2.CARDDEMO.DALYREJS", "app/jcl/POST.jcl:6")],
    }


def test_csd_parse(tmp_path):
    p = tmp_path / "X.CSD"
    p.write_text(" DEFINE FILE(ACCTDAT) GROUP(G)\n"
                 "        DSNAME(AWS.M2.CARDDEMO.ACCTDATA.VSAM.KSDS) RLSACCESS(NO)\n"
                 " DEFINE TRANSACTION(CAVW) GROUP(G)\n"
                 "        PROGRAM(COACTVWC) TWASIZE(0)\n")
    info = inv.parse_csd(p, "app/csd/X.CSD")
    assert info.file_dsn == {"ACCTDAT": ("AWS.M2.CARDDEMO.ACCTDATA.VSAM.KSDS", "app/csd/X.CSD:1")}
    assert info.transactions["COACTVWC"] == [("CAVW", "app/csd/X.CSD:3")]


CLONE = Path(os.environ.get("CARDDEMO_REF", "~/projects/reference/carddemo")).expanduser()


@pytest.mark.skipif(not (CLONE / "app" / "cbl").is_dir(), reason="reference clone not available")
def test_against_reference_clone():
    programs = inv.Scanner(CLONE).scan()
    assert not any(name.startswith("COPAU") for name in programs)  # app-* modules excluded
    view = programs["COACTVWC"]
    assert view.type == "online" and view.transactions == ["CAVW"]
    assert set(view.files) == {"ACCTDAT", "CUSTDAT", "CXACAIX"}
    assert view.maps == ["COACTVW(CACTVWA)"]
    assert "COMEN01C" in view.calls
    post = programs["CBTRN02C"]
    assert post.type == "batch" and post.jcl == ["app/jcl/POSTTRAN.jcl"]
    assert post.files["ACCTFILE"] == {"I-O"}
    assert programs["CSUTLDTC"].type == "online"  # only called from CICS programs
    assert "COPAUS0C" in programs["COMEN01C"].calls


def test_line_numbers_count_lf_only(tmp_path):
    # A form feed must not start a new line, or citations drift from check_citations.
    s = src(tmp_path, "       01 A PIC X.\f\r\n       01 B PIC X(8) VALUE 'B'.\r\n")
    assert {d.name: d.cite for d in inv.parse_definitions(s)}["B"] == "app/cbl/PROG1.cbl:2"
