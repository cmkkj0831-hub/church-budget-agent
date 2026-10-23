"""반영 단위 이력과 되돌리기."""
from datetime import date

import pytest

from core.changes import record_unit, snapshot, undo_unit, units
from core.issues import IssueBook, apply_resolution, commit_with_issues
from core.ledger import BlockedError, Ledger
from core.models import LedgerChange, LineItem, RequestDoc, Resolution
from tests.conftest import TODAY

WHO = "회계담당"


def generate(ledger, book):
    base = snapshot(ledger, book)
    res = ledger.build_request(RequestDoc(월=11, 사업ID="P11", 행사명="11월 교사회 운영비", 작성일=date(2026, 11, 1),
                                          세부내역=[LineItem(내용="운영비", 단가=60_000, 수량=9)]), today=TODAY)
    rows, issues = commit_with_issues(ledger, book, res, accept_warnings=True)
    return record_unit(ledger, book, base, "장부: 신청서 추가", WHO), rows, issues


def test_record_and_undo_generation(ledger, book, data_dir):
    cum = ledger.cumulative_requested()
    unit, rows, issues = generate(ledger, book)
    assert issues  # P11 예산 경고가 이슈로 자동 등록
    entries = [h for h in book.history if h.이력ID == unit]
    assert {(h.대상, h.열) for h in entries} == {("장부", "(행 추가)"), ("이슈", "(이슈 추가)")}
    ledger.save(); book.save()

    L, B = Ledger.load(data_dir), IssueBook.load(data_dir)
    undo = undo_unit(L, B, unit, WHO)
    assert len(L.rows) == 53 and L.cumulative_requested() == cum
    assert all(i.이슈ID != issues[0].이슈ID for i in B.issues)  # 자동 등록 이슈도 함께 복원
    assert L.next_doc_number() == 53  # 번호도 다시 쓸 수 있다
    u = {x["이력ID"]: x for x in units(B)}
    assert u[unit]["되돌림"] and u[undo]["사유"] == f"되돌리기: {unit}"
    with pytest.raises(BlockedError):  # 두 번은 안 됨
        undo_unit(L, B, unit, WHO)


def test_undo_resolution_restores_ledger_and_issues(ledger, book):
    base = snapshot(ledger, book)
    apply_resolution(ledger, book, "I-019", Resolution(
        방식="설명", 내용="철회", 처리자=WHO, 처리일=TODAY,
        변경=[LedgerChange(순번=38, 열="상태", 값="취소")], 함께해결=["I-018"]))
    unit = record_unit(ledger, book, base, "이슈: I-019 해결(설명됨)", WHO, "I-019")
    assert ledger.row(38).상태 == "취소"
    undo_unit(ledger, book, unit, WHO)
    assert ledger.row(38).상태 == "보고서없음"
    assert book.get("I-019").상태 == book.get("I-018").상태 == "미해결"
    assert book.get("I-019").해결내용 == "" and book.get("I-019").처리일 is None
    assert 38 in book.ledger_remarks()


def test_undo_refused_when_changed_later(ledger, book):
    base = snapshot(ledger, book)
    ledger.row(53).행사명 = "10월 교사회 운영비"
    unit = record_unit(ledger, book, base, "장부: 행사명 정정", WHO)
    base2 = snapshot(ledger, book)
    ledger.row(53).행사명 = "10월 운영비"
    record_unit(ledger, book, base2, "장부: 다시 정정", WHO)
    with pytest.raises(BlockedError, match="다시 바뀌어"):
        undo_unit(ledger, book, unit, WHO)


def test_no_change_no_unit(ledger, book):
    assert record_unit(ledger, book, snapshot(ledger, book), "x", WHO) is None
