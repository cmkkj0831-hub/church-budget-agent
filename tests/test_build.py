"""신청서·보고서 생성과 사후정산."""
from datetime import date

import pytest

from core.issues import commit_with_issues
from core.ledger import BlockedError, Ledger
from core.models import LineItem, ReportDoc, RequestDoc
from tests.conftest import TODAY


def report_draft(amount, **kw):
    return ReportDoc(월=10, 사업ID="P12", 행사명="10월 교리 준비비", 작성일=date(2026, 10, 24),
                     세부내역=[LineItem(계정과목="교리준비비", 내용="교리 준비물", 단가=amount)], **kw)


def test_p12_limit(ledger):
    assert ledger.business_spent("P12") == 1_370_145
    assert ledger.business_limit("P12") == 309_855


def test_post_settlement_over_limit(ledger):
    res = ledger.build_post_settlement(report_draft(350_000), today=TODAY)
    req, rep = res.docs
    assert res.errors == []
    assert (req.금번신청액, req.본당보조, req.자체예산, req.합계) == (309_855, 309_855, 40_145, 350_000)
    assert (rep.본당보조, rep.자체예산, rep.예산A, rep.합계B, rep.잔액) == (309_855, 40_145, 350_000, 350_000, 0)
    assert (req.문서번호, rep.문서번호) == ("(예)2026-053", "(집)2026-054")
    assert req.작성일 == rep.작성일 == date(2026, 10, 24)
    assert req.신청누계액 == 23_694_010 + 309_855

    rows = ledger.commit(res)
    assert [r.상태 for r in rows] == ["사후정산", "사후정산"]
    assert rows[0].연결문서번호 == rows[1].문서번호 and rows[1].연결문서번호 == rows[0].문서번호
    assert [r.파일명 for r in rows] == ["2_10월 교리 준비비_신청서.hwpx", "3_10월 교리 준비비_보고서.hwpx"]
    assert ledger.business_limit("P12") == 0
    assert ledger.settings.금액문서기준.다음문서번호 == 55


def test_post_settlement_under_limit(ledger):
    req, rep = ledger.build_post_settlement(report_draft(100_000), today=TODAY).docs
    assert (req.금번신청액, req.자체예산) == (100_000, 0)
    assert (rep.본당보조, rep.자체예산, rep.잔액) == (100_000, 0, 0)


def test_post_settlement_exhausted(ledger):
    ledger.commit(ledger.build_post_settlement(report_draft(400_000), today=TODAY))
    with pytest.raises(BlockedError):
        ledger.build_post_settlement(report_draft(1_000), today=TODAY)


def test_build_request_operating_cost(ledger, book, data_dir):
    s = ledger.settings.금액문서기준
    draft = RequestDoc(월=11, 사업ID="P11", 행사명="11월 교사회 운영비", 작성일=date(2026, 11, 1),
                       세부내역=[LineItem(계정과목="교사회 운영비", 내용="교사회 운영비",
                                          단가=s.운영비단가, 수량=s.교사수)])
    res = ledger.build_request(draft, today=TODAY)
    doc = res.docs[0]
    assert (doc.합계, doc.본당보조, doc.금번신청액, doc.문서번호) == (540_000, 540_000, 540_000, "(예)2026-053")
    assert doc.신청누계액 == 23_694_010 + 540_000
    assert doc.잔여액 == 36_470_000 - doc.신청누계액
    assert res.errors == []
    assert [w.유형 for w in res.warnings] == ["예산 경고"]  # P11 계획 초과 + 연말 예상 초과(합쳐짐)
    assert "연말 예상 5,678,000원" in res.warnings[0].설명  # 4,598,000 + 11월 540,000 + 12월 540,000

    with pytest.raises(BlockedError):
        ledger.commit(res)  # 경고 미확인
    expected_id = book.next_id()
    rows, issues = commit_with_issues(ledger, book, res, accept_warnings=True)
    assert rows[0].상태 == "진행중" and rows[0].파일명 == "1_11월 교사회 운영비_신청서.hwpx"
    assert issues[0].이슈ID == expected_id and issues[0].대상순번 == [54] and issues[0].유형 == "예산 경고"
    assert 54 in book.ledger_remarks()

    ledger.save(); book.save()
    again = Ledger.load(data_dir)
    assert again.row(54).신청누계액 == doc.신청누계액
    assert again.settings.금액문서기준.다음문서번호 == 54


@pytest.mark.parametrize("used, subsidy, own, balance", [
    (500_000, 500_000, 0, 40_000),   # 미사용: 본당 = 실사용, 반납 40,000
    (600_000, 540_000, 60_000, 0),   # 초과: 본당 = 신청, 초과분 자체
])
def test_build_report_from_request(ledger, used, subsidy, own, balance):
    req = ledger.row(53)  # 10월 운영비 신청 540,000
    draft = ReportDoc(월=10, 사업ID="P11", 행사명="10월 교사회 운영비", 작성일=date(2026, 10, 31),
                      세부내역=[LineItem(내용="회합 후 식대", 단가=used)])
    res = ledger.build_report(draft, req, today=TODAY)
    rep = res.docs[0]
    assert (rep.본당보조, rep.자체예산, rep.합계B, rep.잔액) == (subsidy, own, used, balance)
    assert rep.합계B == rep.본당보조 + rep.자체예산
    assert rep.예산A == 540_000 + own and rep.예산A - rep.합계B == rep.잔액
    assert rep.연결문서번호 == "(예)2026-052"
    assert res.errors == []
    before = ledger.cumulative_requested()
    ledger.commit(res, accept_warnings=True)
    assert ledger.row(53).연결문서번호 == rep.문서번호 and ledger.row(53).상태 == "정산완료"
    assert ledger.cumulative_requested() == before - balance


def test_errors_block_commit(ledger):
    draft = RequestDoc(월=11, 사업ID="P12", 행사명="11월 교리 준비비", 작성일=date(2026, 11, 1),
                       세부내역=[LineItem(내용="교재", 단가=10_000, 수량=3, 금액=20_000)])
    res = ledger.build_request(draft, today=TODAY)
    assert [e.코드 for e in res.errors] == ["line_amount"]
    with pytest.raises(BlockedError):
        ledger.commit(res, accept_warnings=True)
    assert len(ledger.rows) == 53


def test_annual_budget_blocks(ledger):
    draft = RequestDoc(월=12, 사업ID="P10", 행사명="성탄절 행사", 작성일=date(2026, 12, 1),
                       세부내역=[LineItem(내용="행사비", 단가=13_000_000)])
    res = ledger.build_request(draft, today=TODAY)
    assert "annual_budget" in [e.코드 for e in res.errors]
