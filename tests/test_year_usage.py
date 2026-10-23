"""회계연도 전환, 사용량 집계."""
import json

import pytest

from core.issues import IssueBook
from core.ledger import BlockedError, Ledger, load_settings, read_csv
from core.models import UsagePricing
from core.usage import annual_projection, summarize
from core.year import start_year

# ---------------------------------------------------------------- 회계연도 전환

def test_start_new_year(data_dir):
    prev_plan = read_csv(data_dir / "2026" / "plan.csv")
    plan = [{**p, "본당예산": int(p["본당예산"]), **{f"{m}월": int(p[f"{m}월"]) for m in range(1, 13)}}
            for p in prev_plan]
    carry = [i for i in IssueBook.load(data_dir).issues if i.이슈ID in ("I-019", "I-026")]
    s = start_year(data_dir, 2027, plan, [{"항목": "사업목표", "내용": "x"}], 37_000_000, carry, 2026)
    assert s.현재연도 == 2027 and s.year_info(2026).마감 and not s.year_info(2027).마감
    assert json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))["기본정보"]["회계연도"] == 2027

    L, B = Ledger.load(data_dir), IssueBook.load(data_dir)
    assert (L.year, L.rows, L.allocation, L.next_doc_number()) == (2027, [], 37_000_000, 1)
    assert L.format_doc_number(1, "예산신청서") == "(예)2027-001"
    assert L.remaining() == 37_000_000 and len(L.plan) == 15
    assert [i.이슈ID for i in B.issues] == ["I-001", "I-002"]
    assert B.issues[0].설명.startswith("[2026 이월]") and "2026 (예)2026-037" in B.issues[0].대상문서번호
    assert (data_dir / "2027" / "usage.csv").exists() and (data_dir / "2027" / "history.csv").exists()

    old = Ledger.load(data_dir, 2026)
    assert old.read_only and len(old.rows) == 53
    with pytest.raises(BlockedError):
        old.save()
    with pytest.raises(BlockedError):
        start_year(data_dir, 2027, plan, [], 1, [], 2026)  # 이미 있음


def test_start_year_rejects_bad_plan(data_dir):
    bad = [{"사업ID": "P01", "시기": "", "계속신규": "", "사업명": "x", "세부내역": "", "자체예산": 0,
            "본당예산": 100, "전년도집행액": "", **{f"{m}월": 0 for m in range(1, 13)}}]
    with pytest.raises(BlockedError, match="월별 배분"):
        start_year(data_dir, 2027, bad, [], 100)
    assert load_settings(data_dir).현재연도 == 2026


# ---------------------------------------------------------------- 사용량

def test_usage_summary_and_projection():
    rows = [{"시각": "2026-10-01T10:00:00", "기능": "영수증 읽기", "모델": "m", "요청모델": "m", "대체모델": "",
             "입력토큰": "1000000", "출력토큰": "100000", "소요초": "3", "환경": "prod"},
            {"시각": "2026-11-01T10:00:00", "기능": "영수증 읽기", "모델": "m", "요청모델": "m", "대체모델": "",
             "입력토큰": "1000000", "출력토큰": "100000", "소요초": "3", "환경": "prod"},
            {"시각": "2026-11-02T10:00:00", "기능": "신청서 초안", "모델": "m", "요청모델": "m", "대체모델": "",
             "입력토큰": "500000", "출력토큰": "0", "소요초": "1", "환경": "dev"}]
    p = UsagePricing(입력_USD_1M=2, 출력_USD_1M=10, 환율_KRW_USD=1400)
    s = summarize(rows, p)
    assert [(g["월"], g["기능"], g["환경"], g["예상비용_원"]) for g in s] == [
        ("2026-10", "영수증 읽기", "prod", 4200), ("2026-11", "신청서 초안", "dev", 1400),
        ("2026-11", "영수증 읽기", "prod", 4200)]
    assert annual_projection(rows, p) == {"사용한 달": 2, "지금까지(원)": 8400, "월평균(원)": 4200,
                                          "연간 예상(원)": 50400}
    assert summarize(rows, UsagePricing())[0]["예상비용_원"] == 0  # 단가를 안 넣으면 0
