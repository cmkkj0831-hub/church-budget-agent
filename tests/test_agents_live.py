"""실제 Claude API 스모크 테스트. `pytest tests/test_agents_live.py --live` 로만 실행(비용 발생).
값의 정확도가 아니라 호출·스키마·후처리가 끝까지 도는지만 본다. 정확도는 scripts/eval_extraction.py."""
from datetime import date

import pytest

from agents import receipt_agent, report_agent
from agents.llm import api_key
from agents.receipt_agent import ReceiptExtraction
from core.paths import find_child, source_dir

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _need_key():
    if not api_key():
        pytest.skip("ANTHROPIC_API_KEY 없음")


def show(name, calls, extra=""):
    for c in calls:
        print(f"\n[{name}] {c.line()}{extra}")


def test_live_receipt(settings):
    try:
        folder = find_child(find_child(source_dir(2026), "8월"), "8월 운영비 영수증")
    except FileNotFoundError:
        pytest.skip("원본 영수증 없음")
    out = receipt_agent.extract_receipts([find_child(folder, "8월 운영비 - 얼맥당(8.16).pdf")], settings)
    show("영수증", [out[0].call], f" | {out[0].날짜} {out[0].상호} {out[0].금액}")
    assert (out[0].날짜, out[0].금액) == (date(2026, 8, 16), 43_300)


def test_live_report(settings):
    receipts = [ReceiptExtraction(1, "a.pdf", date(2026, 8, 15), "우리생고기", 281_000, "식사", False),
                ReceiptExtraction(2, "b.pdf", date(2026, 8, 16), "얼맥당", 43_300, "식사", False)]
    comp = report_agent.compose_report(settings, "행사명: 8월 교사회 운영비\n대상 월: 8월\n신청액: 540,000원", receipts,
                                       progress_note="8/15 미사 후 식대 연재, 플로라 / 8/16 회합 후 식대 연재")
    show("보고서", comp.calls, f" | {comp.경과보고!r}")
    assert sum(i.단가 for i in comp.세부내역) == 324_300 and "8월 15일" in comp.경과보고
