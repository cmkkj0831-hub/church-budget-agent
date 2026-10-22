"""이슈 해결 흐름(임시 복사본에서 실행)."""
import shutil

import pytest

import core.issues as issues_mod
import core.ledger as ledger_mod
from core.ledger import derive_remarks, read_csv
from core.paths import HISTORY_CSV, ISSUES_CSV, LEDGER_CSV


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    paths = {}
    for name, src in (("LEDGER_CSV", LEDGER_CSV), ("ISSUES_CSV", ISSUES_CSV), ("HISTORY_CSV", HISTORY_CSV)):
        dst = tmp_path / src.name
        shutil.copy(src, dst)
        paths[name] = dst
        monkeypatch.setattr(ledger_mod, name, dst)
        monkeypatch.setattr(issues_mod, name, dst)
    return paths


def test_resolve_with_correction_records_history(tmp_data):
    before = len(read_csv(tmp_data["HISTORY_CSV"]))
    issues_mod.resolve_issue("I-001", "해결-정정", "장부 정정", "테스트", "tester",
                             [(1, "문서번호", "(예)2026-001")])
    row = next(r for r in read_csv(tmp_data["LEDGER_CSV"]) if r["순번"] == "1")
    assert row["문서번호"] == "(예)2026-001"
    h = read_csv(tmp_data["HISTORY_CSV"])
    assert len(h) == before + 1 and h[-1]["변경전"] == "(미기재)"
    issues = read_csv(tmp_data["ISSUES_CSV"])
    assert next(i for i in issues if i["이슈ID"] == "I-001")["상태"] == "해결-정정"
    assert "문서번호 미기재" not in derive_remarks(issues).get(1, "")


def test_resolve_rejects_non_target_and_missing_changes(tmp_data):
    with pytest.raises(ValueError):
        issues_mod.resolve_issue("I-001", "해결-정정", "x", "x", "t", [(2, "문서번호", "x")])
    with pytest.raises(ValueError):
        issues_mod.resolve_issue("I-001", "해결-정정", "x", "x", "t", [])
    with pytest.raises(ValueError):
        issues_mod.resolve_issue("I-009", "해결-설명됨", "x", "x", "t")  # 이미 해결됨
