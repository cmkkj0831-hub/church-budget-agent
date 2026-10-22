"""회귀: 올해 장부를 처음부터 재계산해 문서에 적힌 누계와 비교한다.
차이가 생기는 곳은 모두 등록된 이슈와 대응해야 한다."""
from datetime import date

from core.issues import cumulative_reconciliation
from core.ledger import report_amounts
from core.models import LineItem, ReportDoc
from core.validate import scan_ledger
from tests.conftest import TODAY

CUMULATIVE_TYPES = {"누계 누락", "계산 불일치"}


def _breaks(ledger):
    return ledger.cumulative_breaks()


def test_every_cumulative_difference_has_issue(ledger, book):
    unexplained = [
        seq for seq in _breaks(ledger)
        if not any(i.유형 in CUMULATIVE_TYPES and seq in i.대상순번 for i in book.issues)
    ]
    assert unexplained == []


def test_known_cumulative_breaks(ledger):
    # 32: 신앙학교 준비비 1,300,000 누락(+같은 날 반납 29,705 시점), 37: 32의 오류를 되돌리며 2,850 초과,
    # 38: 부서 운영비 480,000 미반영. 이후는 차이가 그대로 이어질 뿐 새 차이 없음.
    assert _breaks(ledger) == {32: -1_329_705, 37: 1_332_555, 38: -480_000}
    last = ledger.cumulative_trace()[-1]
    assert (last.순번, last.기재, last.계산) == (53, 23_216_860, 23_694_010)


def test_remaining_differences_have_issue(ledger, book):
    for r in ledger.requests():
        if r.예산배정액 - r.신청누계액 != r.잔여액:
            assert any(i.유형 == "계산 불일치" and r.순번 in i.대상순번 for i in book.issues), r.순번


def test_current_totals(ledger):
    assert ledger.cumulative_requested() == 23_694_010
    assert ledger.remaining() == 36_470_000 - 23_694_010
    assert ledger.next_doc_number() == 53


def test_scan_finds_no_unregistered(ledger, book):
    """올해 장부의 이상은 모두 이슈로 등록돼 있어야 한다."""
    assert scan_ledger(ledger, book.issues, today=TODAY) == []


def test_explained_issue_covers_only_its_codes(ledger, book):
    """순번 21·22(I-024·025, 해결-설명됨)는 본당란 불일치만 덮는다. 다른 오류는 다시 잡힌다."""
    assert {book.get(i).상태 for i in ("I-024", "I-025")} == {"해결-설명됨"}
    assert 21 not in book.ledger_remarks() and 22 not in book.ledger_remarks()
    ledger.row(21).잔액 = 0  # 반납액을 틀리게 바꾸면 balance 검사는 걸려야 한다
    found = scan_ledger(ledger, book.issues, today=TODAY)
    assert ([21], "balance") in [(f.순번, f.코드) for f in found]
    assert not any(f.순번 == [21] and f.코드 in ("report_sum", "subsidy_vs_request") for f in found)


# ---------------------------------------------------------------- 보고서 금액 규칙

def test_report_rule_refund_case(ledger):
    """반납: 순번 34 6월 교리준비비 보고서(신청 420,000, 실사용 390,295)."""
    draft = ReportDoc(월=6, 사업ID="P12", 행사명="6월 교리준비비", 작성일=date(2026, 6, 28),
                      세부내역=[LineItem(내용="6월 3학년 교리준비비", 단가=180_695),
                               LineItem(내용="6월 14일 6학년 특별 교리비", 단가=209_600)])
    rep = ledger.build_report(draft, ledger.row(30), today=TODAY).docs[0]
    assert (rep.본당보조, rep.자체예산, rep.합계B, rep.예산A, rep.잔액) == (390_295, 0, 390_295, 420_000, 29_705)
    row = ledger.row(34)
    assert (row.본당보조, row.자체예산, row.합계, row.잔액) == (rep.본당보조, rep.자체예산, rep.합계B, rep.잔액)


def test_report_rule_overspend_case(ledger):
    """초과: 순번 15 3월 운영비 보고서(신청 480,000, 실사용 518,000 → A 518,000 = 480,000 + 자체 38,000)."""
    draft = ReportDoc(월=3, 사업ID="P11", 행사명="교사회 운영비", 작성일=date(2026, 4, 5),
                      세부내역=[LineItem(내용="3월 7일 미사 후 식대", 단가=314_000),
                               LineItem(내용="3월 14일 미사 전 영화 관람", 단가=70_000),
                               LineItem(내용="3월 14일 미사 전 식사", 단가=88_000),
                               LineItem(내용="3월 28일 미사 후 식대", 단가=46_000)])
    rep = ledger.build_report(draft, ledger.row(13), today=TODAY).docs[0]
    assert (rep.본당보조, rep.자체예산, rep.합계B, rep.예산A, rep.잔액) == (480_000, 38_000, 518_000, 518_000, 0)
    row = ledger.row(15)
    assert (row.본당보조, row.자체예산, row.합계, row.잔액) == (rep.본당보조, rep.자체예산, rep.합계B, rep.잔액)


def test_report_rule_against_all_new_form_reports(ledger, book):
    """신 양식 보고서 전부를 규칙으로 재계산. 다른 값은 등록 이슈로 설명돼야 한다."""
    for r in ledger.reports():
        req = ledger.find_ref(r.연결문서번호)
        if r.양식 != "신" or req is None:
            continue
        subsidy, own, total, _, balance = report_amounts(req.금번신청액, r.합계)
        if (r.본당보조, r.자체예산, r.잔액) != (subsidy, own, balance):
            assert any(r.순번 in i.대상순번 for i in book.issues), r.순번


# ---------------------------------------------------------------- 누계 두 기준

def test_cumulative_two_bases(ledger, book):
    rec = cumulative_reconciliation(ledger, book)
    s = rec.summary
    assert (s.문서기준, s.재계산, s.차이) == (23_216_860, 23_694_010, -477_150)
    assert (s.문서기준_잔여, s.재계산_잔여) == (13_253_140, 12_775_990)
    assert sum(item["차이"] for item in rec.항목) == s.차이
    assert rec.설명됨 and all(item["이슈"] for item in rec.항목)
