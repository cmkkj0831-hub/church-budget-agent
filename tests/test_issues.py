"""이슈 해결: 부서 운영비(순번 38)를 두 가지 방식으로 해결."""
from datetime import date

import pytest

from core.issues import IssueBook, apply_resolution, cumulative_reconciliation
from core.ledger import BlockedError, Ledger
from core.models import LedgerChange, LedgerRow, Resolution
from core.validate import scan_ledger
from tests.conftest import TODAY

CUM, ALLOC = 23_694_010, 36_470_000
WHO = "회계관리자"


def descs(book, seq):
    return book.ledger_remarks().get(seq, "")


def test_initial_remarks(book):
    r38 = descs(book, 38)
    for iid in ("I-017", "I-018", "I-019", "I-020"):
        assert book.get(iid).설명 in r38
    assert 19 not in book.ledger_remarks()  # I-009 해결됨
    kinds = dict(book.ledger_remarks_detail()[53])  # 유형 → 설명
    assert set(kinds) == {"기재 오류", "예산 경고"}


def test_resolution_a_withdrawn(ledger, book, data_dir):
    """해결 A: 철회됨 → 상태 취소(설명). 누계에서 480,000원이 빠진다."""
    res = apply_resolution(ledger, book, "I-019", Resolution(
        방식="설명", 내용="철회됨: 부서 운영비 신청 철회, 지급 없음", 처리자=WHO, 처리일=TODAY,
        변경=[LedgerChange(순번=38, 열="상태", 값="취소")], 함께해결=["I-018"]))

    assert (res.누계_전, res.누계_후, res.잔여_후) == (CUM, CUM - 480_000, ALLOC - CUM + 480_000)
    assert res.diff == [{"순번": 38, "열": "상태", "변경전": "보고서없음", "변경후": "취소"}]
    row = ledger.row(38)
    assert row.상태 == "취소" and row.금번신청액 == 480_000  # 값은 그대로
    assert book.get("I-019").상태 == book.get("I-018").상태 == "해결-설명됨"
    assert book.get("I-018").해결내용.startswith("I-019와 함께 해결")

    r38 = descs(book, 38)
    assert book.get("I-018").설명 not in r38 and book.get("I-019").설명 not in r38
    assert book.get("I-020").설명 in r38 and book.get("I-017").설명 in r38

    all_found = scan_ledger(ledger, book.issues, today=TODAY, include_registered=True)
    assert not any(38 in f.순번 and f.유형 in ("누계 누락", "문서 누락") for f in all_found)
    rec = cumulative_reconciliation(ledger, book)  # 38 단절이 사라지고 남은 차이 2,850은 I-016
    assert (rec.summary.문서기준, rec.summary.재계산, rec.summary.차이) == (23_216_860, CUM - 480_000, 2_850)
    assert [i["순번"] for i in rec.항목] == [32, 37] and rec.설명됨
    assert book.history[-1].변경전 == "보고서없음" and book.history[-1].변경후 == "취소"

    ledger.save(); book.save()
    assert Ledger.load(data_dir).cumulative_requested() == CUM - 480_000
    assert IssueBook.load(data_dir).get("I-019").처리일 == TODAY


def test_resolution_b_paid(ledger, book, data_dir):
    """해결 B: 지급됨 → 보고서 행 추가(정정). 누계는 그대로, 신청서가 정산완료가 된다."""
    report = LedgerRow(월="7월", 구분="집행보고서", 작성일="2026년 10월 23일", 자체예산=0,
                       본당보조=480_000, 합계=480_000, 잔액=0, 행사명="부서 운영비", 사업ID="P13",
                       연결문서번호="(예)2026-037")
    res = apply_resolution(ledger, book, "I-019", Resolution(
        방식="정정", 내용="지급됨: 1~8월분 부서 운영비 지급 확인, 집행보고서 추가", 처리자=WHO,
        처리일=TODAY, 추가행=[report]))

    assert (res.누계_전, res.누계_후, res.잔여_후) == (CUM, CUM, ALLOC - CUM)
    new = ledger.row(54)
    assert (new.문서번호, new.파일명, new.상태) == ("(집)2026-053", "4_부서 운영비_보고서.hwpx", "정산완료")
    req = ledger.row(38)
    assert (req.연결문서번호, req.상태) == ("(집)2026-053", "정산완료")
    assert ledger.settings.금액문서기준.다음문서번호 == 54
    assert [d["열"] for d in res.diff] == ["(행 추가)", "연결문서번호", "상태"]

    assert book.get("I-019").상태 == "해결-정정"
    r38 = descs(book, 38)
    assert book.get("I-019").설명 not in r38
    assert book.get("I-018").설명 in r38  # 문서의 누계 미반영은 여전히 열린 이슈
    assert len([h for h in book.history if h.이슈ID == "I-019"]) == 3

    found = scan_ledger(ledger, book.issues, today=TODAY, include_registered=True)
    assert not any(f.유형 == "문서 누락" for f in found)
    ledger.save(); book.save()
    assert Ledger.load(data_dir).row(54).본당보조 == 480_000


def test_guards(ledger, book):
    with pytest.raises(BlockedError):  # 설명은 값 변경 불가
        apply_resolution(ledger, book, "I-019", Resolution(
            방식="설명", 내용="x", 처리자=WHO, 변경=[LedgerChange(순번=38, 열="금번신청액", 값=0)]))
    with pytest.raises(BlockedError):  # 정정에는 변경 필요
        apply_resolution(ledger, book, "I-019", Resolution(방식="정정", 내용="x", 처리자=WHO))
    with pytest.raises(BlockedError):  # 이미 해결된 이슈
        apply_resolution(ledger, book, "I-009", Resolution(방식="설명", 내용="x", 처리자=WHO))
    with pytest.raises(BlockedError):  # 대상 밖 순번
        apply_resolution(ledger, book, "I-019", Resolution(
            방식="정정", 내용="x", 처리자=WHO, 변경=[LedgerChange(순번=1, 열="상태", 값="취소")]))


def test_failed_verification_leaves_ledger_untouched(ledger, book):
    bad = LedgerRow(월="7월", 구분="집행보고서", 작성일="2026년 10월 23일", 자체예산=0,
                    본당보조=480_000, 합계=400_000, 잔액=0, 행사명="부서 운영비", 사업ID="P13",
                    연결문서번호="(예)2026-037")
    with pytest.raises(BlockedError):
        apply_resolution(ledger, book, "I-019", Resolution(
            방식="정정", 내용="x", 처리자=WHO, 추가행=[bad]))
    assert len(ledger.rows) == 53 and ledger.row(38).상태 == "보고서없음"
    assert book.get("I-019").상태 == "미해결" and len(book.history) == 1


def test_correction_of_value(ledger, book):
    """I-022 행사명 정정: 값 변경과 이력."""
    res = apply_resolution(ledger, book, "I-022", Resolution(
        방식="정정", 내용="10월분 행사명 정정", 처리자=WHO, 처리일=date(2026, 10, 23),
        변경=[LedgerChange(순번=53, 열="행사명", 값="10월 교사회 운영비")]))
    assert res.diff[0]["변경전"] == "9월 교사회 운영비"
    assert ledger.row(53).행사명 == "10월 교사회 운영비"
    assert book.get("I-022").설명 not in book.ledger_remarks()[53]
    assert book.ledger_remarks()[53] == book.get("I-023").설명  # 예산 경고만 남음
