"""올해 실제 오류를 재현해 검증이 잡는지 확인."""
from datetime import date, datetime

from core.models import LineItem, ReportDoc, RequestDoc
from core.validate import scan_ledger, validate_report, validate_request
from tests.conftest import TODAY


def codes(findings):
    return {f.코드 for f in findings}


def scan_all(ledger, book):
    return scan_ledger(ledger, book.issues, today=TODAY, include_registered=True)


def test_march_catechism_total_700000(ledger, book):
    doc = RequestDoc(월=3, 사업ID="P12", 행사명="3월 교리 준비비", 작성일=date(2026, 4, 5),
                     문서번호="(예)2026-090",
                     세부내역=[LineItem(계정과목="교리준비비", 내용="교리준비비", 단가=23_400, 금액=23_400)],
                     수입=23_400, 자체예산=0, 본당보조=23_400, 합계=700_000, 금번신청액=23_400)
    found = validate_request(doc, ledger, today=TODAY)
    assert {"detail_sum", "income_sum"} <= codes(found)
    assert all(f.수준 == "오류" for f in found if f.코드 in {"detail_sum", "income_sum"})
    row14 = [f for f in scan_all(ledger, book) if f.순번 == [14]]
    assert row14 and row14[0].유형 == "계산 불일치"


def test_childrens_day_detail_sums(ledger, book):
    req = RequestDoc(월=5, 사업ID="P05", 행사명="초등부 어린이 날 행사", 작성일=date(2026, 5, 9),
                     문서번호="(예)2026-091",
                     세부내역=[LineItem(내용="행사 상품 구매", 단가=160_000, 금액=160_000),
                              LineItem(내용="행사 부스 물품 구매", 단가=56_000, 금액=56_000),
                              LineItem(내용="행사 간식 준비", 단가=110_000, 금액=110_000),
                              LineItem(내용="기타 비용", 단가=174_000, 금액=174_000)],
                     자체예산=0, 본당보조=490_000, 합계=490_000, 금번신청액=490_000)
    assert "detail_sum" in codes(validate_request(req, ledger, today=TODAY))

    rep = ReportDoc(월=5, 사업ID="P05", 행사명="초등부 어린이 날 행사", 작성일=date(2026, 5, 9),
                    문서번호="(집)2026-092",
                    세부내역=[LineItem(내용="추첨권 상품", 단가=132_030, 금액=132_030),
                             LineItem(내용="행사 간식", 단가=115_300, 금액=115_300),
                             LineItem(내용="행사 부스 물품", 단가=53_240, 금액=53_240),
                             LineItem(내용="행사 회의 후 식대", 단가=157_500, 금액=157_500),
                             LineItem(내용="행사 회의 비용", 단가=32_000, 금액=32_000)],
                    예산A=490_060, 자체예산=60, 본당보조=490_060, 합계B=490_060, 잔액=0)
    found = validate_report(rep, ledger, request=ledger.row(24), today=TODAY)
    assert {"detail_sum", "budget_a", "report_sum", "subsidy_vs_request"} <= codes(found)
    assert any(f.순번 == [25] and f.유형 == "계산 불일치" for f in scan_all(ledger, book))


def test_october_named_september(ledger, book):
    doc = RequestDoc(월=10, 사업ID="P11", 행사명="9월 교사회 운영비", 작성일=date(2026, 10, 10),
                     세부내역=[LineItem(내용="교사회 운영비", 단가=60_000, 수량=9)])
    res = ledger.build_request(doc, today=TODAY)
    assert "name_month" in codes(res.warnings)
    assert any(f.코드 == "name_month" and f.순번 == [53] for f in scan_all(ledger, book))


def test_dates_in_2025(ledger, book):
    doc = ReportDoc(월=1, 사업ID="P11", 행사명="1월 교사회 운영비", 작성일=date(2025, 1, 31),
                    시작=datetime(2025, 1, 1, 10), 종료=datetime(2025, 1, 31, 16),
                    세부내역=[LineItem(내용="회합 후 식대", 단가=329_500)])
    res = ledger.build_report(doc, ledger.row(2), today=TODAY)
    assert {"date_year", "period_year"} <= codes(res.warnings)
    hits = sorted(f.순번[0] for f in scan_all(ledger, book) if "2025년" in f.설명)
    assert hits == [3, 4, 9]


def test_duplicate_numbers(ledger, book):
    dups = sorted(f.순번 for f in scan_all(ledger, book) if f.코드 == "dup_doc_no")
    assert dups == [[4, 5], [36, 37]]


def test_unreported_requests(ledger, book):
    hits = [f.순번 for f in scan_ledger(ledger, book.issues, today=TODAY, include_registered=True)
            if f.유형 == "문서 누락"]
    assert hits == [[38]]
    in_july = scan_ledger(ledger, book.issues, today=date(2026, 7, 20), include_registered=True)
    assert not any(f.유형 == "문서 누락" for f in in_july)


def test_scan_dedupes_registered(ledger, book):
    new = scan_ledger(ledger, book.issues, today=TODAY)
    assert not any(f.순번 in ([14], [25], [38], [4, 5]) for f in new)
