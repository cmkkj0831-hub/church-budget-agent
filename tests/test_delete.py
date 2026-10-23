"""문서 삭제: 앱에서 만든 문서만, 짝 문서 처리, 이슈 닫기, 이력·되돌리기, 경고."""
from datetime import date

import pytest

from core.changes import record_unit, snapshot, undo_unit
from core.delete import apply_delete, deletable, plan_delete
from core.issues import IssueBook, commit_with_issues
from core.ledger import BlockedError, Ledger
from core.models import LineItem, ReportDoc, RequestDoc
from tests.conftest import TODAY

WHO = "회계담당"


def unit_of(ledger, book, res, message):
    base = snapshot(ledger, book)
    commit_with_issues(ledger, book, res, accept_warnings=True)
    return record_unit(ledger, book, base, message, WHO)


def request(ledger, book, amount=60_000, pid="P11", name="11월 교사회 운영비"):
    res = ledger.build_request(RequestDoc(월=11, 사업ID=pid, 행사명=name, 작성일=date(2026, 11, 1),
                                          세부내역=[LineItem(내용="운영비", 단가=amount, 수량=9)]), today=TODAY)
    unit_of(ledger, book, res, "장부: 신청서 추가")
    return ledger.rows[-1]


def report(ledger, book, req, used):
    draft = ReportDoc(월=req.월번호, 사업ID=req.사업ID, 행사명=req.행사명, 작성일=date(2026, 11, 30),
                      세부내역=[LineItem(내용="식대", 단가=used)])
    unit_of(ledger, book, ledger.build_report(draft, req, today=TODAY), "장부: 보고서 추가")
    return ledger.rows[-1]


def delete(ledger, book, seqs, 사유="잘못 만듦"):
    plan = plan_delete(ledger, book, seqs)
    base = snapshot(ledger, book)
    apply_delete(ledger, book, plan, 사유, WHO, TODAY)
    return plan, record_unit(ledger, book, base, "장부: 문서 삭제", WHO)


def test_original_rows_cannot_be_deleted(ledger, book):
    assert deletable(ledger, book) == []
    with pytest.raises(BlockedError, match="원본 장부"):
        plan_delete(ledger, book, [53])


def test_delete_request_takes_report_and_closes_issues(ledger, book, data_dir):
    cum, rows = ledger.cumulative_requested(), len(ledger.rows)
    req = request(ledger, book)  # P11 예산 경고 → 이슈 자동 등록
    auto = [i.이슈ID for i in book.open_issues() if req.순번 in i.대상순번]
    assert auto
    rep = report(ledger, book, req, 500_000)
    assert [r.순번 for r in deletable(ledger, book)] == [req.순번, rep.순번]

    plan, unit = delete(ledger, book, [req.순번])
    assert plan.seqs == [req.순번, rep.순번] and any("함께 지웁니다" in w for w in plan.warnings)
    assert len(ledger.rows) == rows and ledger.cumulative_requested() == cum
    assert ledger.next_doc_number() == 53  # 마지막 번호였으므로 다시 쓴다
    for iid in auto:
        i = book.get(iid)
        assert i.상태 == "해결-설명됨" and i.해결내용.startswith("문서 삭제((예)2026-053, (집)2026-054): 잘못 만듦")
    ledger.save(); book.save()

    L, B = Ledger.load(data_dir), IssueBook.load(data_dir)
    undo_unit(L, B, unit, WHO)
    assert [r.문서번호 for r in L.rows[-2:]] == ["(예)2026-053", "(집)2026-054"]
    assert all(B.get(iid).열림 for iid in auto)


def test_delete_report_only_restores_request(ledger, book):
    req = request(ledger, book)
    rep = report(ledger, book, req, 500_000)
    assert ledger.row(req.순번).상태 == "정산완료"
    plan, _ = delete(ledger, book, [rep.순번])
    assert plan.seqs == [rep.순번]
    r = ledger.row(req.순번)
    assert (r.연결문서번호, r.상태) == ("", "진행중") and ledger.next_doc_number() == 54


def test_report_on_original_request_restores_original_state(ledger, book):
    orig = ledger.row(53)  # 원본 신청서 (예)2026-052
    before = (orig.연결문서번호, orig.상태)
    rep = report(ledger, book, orig, 500_000)
    assert ledger.row(53).연결문서번호 == rep.문서번호
    delete(ledger, book, [rep.순번])
    assert (ledger.row(53).연결문서번호, ledger.row(53).상태) == before


def test_post_settlement_pair_deleted_together(ledger, book):
    draft = ReportDoc(월=10, 사업ID="P12", 행사명="10월 교리 준비비", 작성일=date(2026, 10, 24),
                      세부내역=[LineItem(계정과목="교리준비비", 내용="교리 준비물", 단가=100_000)])
    unit_of(ledger, book, ledger.build_post_settlement(draft, today=TODAY), "장부: 사후정산")
    req, rep = ledger.rows[-2:]
    plan, _ = delete(ledger, book, [rep.순번])  # 보고서만 골라도 같은 반영의 신청서까지
    assert plan.seqs == [req.순번, rep.순번]


def test_middle_delete_warns_cumulative_and_number_hole(ledger, book):
    first = request(ledger, book, 10_000, "P12", "11월 교리 간식")
    request(ledger, book, 10_000, "P12", "11월 교리 준비물")
    plan = plan_delete(ledger, book, [first.순번])
    text = " ".join(plan.warnings)
    assert "문서에 적힌 신청누계가 재계산보다 90,000원 많아집니다" in text
    assert "(예)2026-053는 빈 번호로 남습니다" in text
    with pytest.raises(BlockedError, match="사유"):
        apply_delete(ledger, book, plan, " ", WHO, TODAY)


def test_closed_year_blocked(ledger, book):
    req = request(ledger, book)
    ledger.year_info.마감 = True
    with pytest.raises(BlockedError, match="마감"):
        plan_delete(ledger, book, [req.순번])
