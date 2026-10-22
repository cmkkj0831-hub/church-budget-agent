"""이관 데이터 무결성 검사."""
import json

import pytest

from core.ledger import (
    ISSUE_STATES, ISSUE_TYPES, LEDGER_COLUMNS, derive_remarks, load_history, load_issues,
    load_ledger, read_csv, target_seqs,
)
from core.migrate import LEDGER_XLSX, read_ledger_xlsx
from core.paths import PLAN_CSV, SETTINGS_JSON, find_child, source_dir


@pytest.fixture(scope="module")
def ledger():
    return load_ledger()


@pytest.fixture(scope="module")
def issues():
    return load_issues()


def test_ledger_columns(ledger):
    assert list(ledger[0].keys()) == LEDGER_COLUMNS
    assert [r["순번"] for r in ledger] == list(range(1, len(ledger) + 1))


def test_ledger_values_match_excel(ledger):
    """장부 값은 원본 그대로여야 한다. 다른 값은 이력에 기록된 정정뿐이다."""
    try:
        src = find_child(source_dir(2026), LEDGER_XLSX)
    except FileNotFoundError:
        pytest.skip("원본 엑셀 없음")
    raw = read_ledger_xlsx(src)
    assert len(raw) == len(ledger)
    history = load_history()
    for x, r in zip(raw, ledger):
        for c in LEDGER_COLUMNS[1:15]:
            expected = x.get(c)
            if expected is None:
                expected = "" if isinstance(r[c], str) else None
            for h in history:
                if int(h["순번"]) == r["순번"] and h["열"] == c:
                    assert str(expected or "") == h["변경전"], (r["순번"], c)
                    expected = type(r[c])(h["변경후"]) if r[c] is not None else None
            assert r[c] == expected, (r["순번"], c)


def test_pairs_symmetric(ledger):
    by_ref = {}
    for r in ledger:
        key = r["문서번호"] if "미기재" not in r["문서번호"] else f"#{r['순번']}"
        by_ref.setdefault(key, []).append(r)
    for r in ledger:
        if r["연결문서번호"]:
            partners = [p for p in by_ref[r["연결문서번호"]] if p["구분"] != r["구분"]]
            assert len(partners) == 1, r["순번"]
            assert partners[0]["사업ID"] == r["사업ID"]


def test_every_row_has_project(ledger):
    plan_ids = {p["사업ID"] for p in read_csv(PLAN_CSV)}
    assert all(r["사업ID"] in plan_ids for r in ledger)


def test_plan_total_matches_allocation():
    plan = read_csv(PLAN_CSV)
    settings = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    assert sum(int(p["본당예산"]) for p in plan) == settings["금액문서기준"]["예산배정액"]
    for p in plan:
        assert sum(int(p[f"{m}월"]) for m in range(1, 13)) == int(p["본당예산"]), p["사업ID"]


def test_settings_has_no_sensitive_values():
    text = SETTINGS_JSON.read_text(encoding="utf-8")
    for word in ("010-", "계좌", "은행", "예금주", "연락처"):
        assert word not in text


def test_issues_valid(issues, ledger):
    seqs = {r["순번"] for r in ledger}
    ids = [i["이슈ID"] for i in issues]
    assert len(ids) == len(set(ids))
    for i in issues:
        assert i["유형"] in ISSUE_TYPES
        assert i["상태"] in ISSUE_STATES
        assert set(target_seqs(i)) <= seqs
        for rel in filter(None, i["관련이슈"].split(";")):
            assert rel in ids
        if i["상태"] == "해결-정정":
            assert any(h["이슈ID"] == i["이슈ID"] for h in load_history())


def test_remarks_follow_open_issues(issues):
    remarks = derive_remarks(issues)
    assert "문서번호 미기재" in remarks[1]
    closed = [dict(i, 상태="해결-정정") if i["대상순번"] == "1" else i for i in issues]
    assert 1 not in derive_remarks(closed)
