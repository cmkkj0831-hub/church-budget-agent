"""회귀: 올해 장부를 처음부터 재계산해 문서에 적힌 누계와 비교한다.
차이가 생기는 곳은 모두 등록된 이슈와 대응해야 한다."""
from core.validate import scan_ledger
from tests.conftest import TODAY

CUMULATIVE_TYPES = {"누계 누락", "계산 불일치"}


def _breaks(ledger):
    """재계산 누계와의 차이가 바뀌는 신청서(오류가 처음 생긴 문서)와 그 변화량."""
    out, prev = {}, 0
    for step in ledger.cumulative_trace():
        if step.차이 != prev:
            out[step.순번] = step.차이 - prev
        prev = step.차이
    return out


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


def test_scan_finds_only_known_new_candidates(ledger, book):
    """등록 이슈로 설명되지 않는 이상은 아래 후보뿐이어야 한다(사용자 판단 대기)."""
    known = {
        ("계산 불일치", (3,)),    # 1월 운영비 보고서: 본당=실사용, 반납 30,500 (예산A − 합계B ≠ 반납)
        ("계산 불일치", (34,)),   # 6월 교리 보고서: 예산(A) 420,000이나 본당 390,295
        ("미확인", (53,)),        # P11 운영비 실집행 4,598,000 > 계획 4,320,000
    }
    found = {(f.유형, tuple(f.순번)) for f in scan_ledger(ledger, book.issues, today=TODAY)}
    assert found <= known
