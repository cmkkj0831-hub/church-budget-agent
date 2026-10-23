"""이관 데이터 무결성 검사(실제 data/ 읽기 전용)."""
import pytest

from core.issues import IssueBook
from core.ledger import LEDGER_COLUMNS, Ledger, read_csv
from core.migrate import LEDGER_XLSX, read_ledger_xlsx
from core.paths import DATA_DIR, SETTINGS_JSON, find_child, source_dir


@pytest.fixture(scope="module")
def real():
    return Ledger.load(DATA_DIR), IssueBook.load(DATA_DIR)


def test_ledger_columns():
    assert list(read_csv(DATA_DIR / "2026" / "ledger.csv")[0].keys()) == LEDGER_COLUMNS


def test_sequence(real):
    ledger, _ = real
    assert [r.순번 for r in ledger.rows] == list(range(1, len(ledger.rows) + 1))


def test_ledger_values_match_excel(real):
    """장부 값은 원본 그대로여야 한다. 다른 값은 이력에 기록된 정정뿐이다."""
    ledger, book = real
    try:
        src = find_child(source_dir(2026), LEDGER_XLSX)
    except FileNotFoundError:
        pytest.skip("원본 엑셀 없음")
    raw = read_ledger_xlsx(src)
    changed = {(h.순번, h.열): h for h in book.history}
    for x, r in zip(raw, ledger.rows):
        for c in LEDGER_COLUMNS[1:15]:
            expected = x.get(c)
            if expected is None and isinstance(getattr(r, c), str):
                expected = ""
            if (r.순번, c) in changed:
                h = changed[(r.순번, c)]
                assert h.변경전 == str(expected or "")
                expected = h.변경후
            assert getattr(r, c) == expected, (r.순번, c)


def test_pairs_symmetric(real):
    ledger, _ = real
    for r in ledger.rows:
        if r.연결문서번호:
            partner = ledger.find_ref(r.연결문서번호)
            assert partner is not None and partner.구분 != r.구분, r.순번
            assert partner.연결문서번호 == r.참조
            assert partner.사업ID == r.사업ID


def test_every_row_has_project(real):
    ledger, _ = real
    assert all(r.사업ID in ledger.plan for r in ledger.rows)


def test_plan_total_matches_allocation(real):
    ledger, _ = real
    assert sum(p.본당예산 for p in ledger.plan.values()) == ledger.allocation
    for p in ledger.plan.values():
        assert sum(p.월별) == p.본당예산, p.사업ID


def test_committed_settings_has_empty_contacts():
    """main에 커밋되는 data/settings.json의 연락처·계좌는 빈칸. 실제 값은 앱이 data 브랜치에만 저장한다."""
    import json
    s = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    assert set(s["연락처"].values()) == {""} and set(s["계좌"].values()) == {""}
    assert "010-" not in SETTINGS_JSON.read_text(encoding="utf-8")


def test_issues_valid(real):
    ledger, book = real
    seqs = {r.순번 for r in ledger.rows}
    ids = [i.이슈ID for i in book.issues]
    assert len(ids) == len(set(ids))
    for i in book.issues:
        assert set(i.대상순번) <= seqs
        assert set(i.관련이슈) <= set(ids)
        if i.상태 == "해결-정정":
            assert any(h.이슈ID == i.이슈ID for h in book.history)


def test_form_column(real):
    """구 양식: 보고서 '반납금액' 칸, 신청서 보좌신부 결재란(원본 문서에서 판별)."""
    ledger, _ = real
    old = [r.순번 for r in ledger.rows if r.양식 == "구"]
    assert old == list(range(1, 19)) + [20]
    assert all(r.양식 in ("구", "신") for r in ledger.rows)
