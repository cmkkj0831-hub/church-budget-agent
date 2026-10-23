"""실제 Claude API 스모크 테스트. `pytest tests/test_agents_live.py --live` 로만 실행(비용 발생).
값의 정확도가 아니라 호출·스키마·후처리가 끝까지 도는지만 본다. 정확도는 scripts/eval_extraction.py."""
from datetime import date

import pytest

from agents import issue_agent, receipt_agent, request_agent
from agents.llm import api_key
from core.paths import find_child, source_dir
from tests.conftest import TODAY

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _need_key():
    if not api_key():
        pytest.skip("ANTHROPIC_API_KEY 없음")


def show(name, calls, extra=""):
    for c in calls:
        print(f"\n[{name}] {c.line()}{extra}")


def test_live_request(ledger):
    d = request_agent.extract_request("12월 20일 성탄 행사, 어린이 120명, 간식 3000원씩, 선물 5000원씩", ledger, TODAY)
    show("신청서", d.calls, f" | 세부 {[(i.내용, i.단가, i.수량) for i in d.doc.세부내역]} 사업ID {d.doc.사업ID} "
                          f"missing {d.missing} defaulted {d.defaulted}")
    assert d.doc.월 == 12 and d.doc.사업ID == "P10"
    assert sorted((i.단가, i.수량) for i in d.doc.세부내역) == [(3000, 120), (5000, 120)]
    assert "장소" in d.defaulted


def test_live_receipt(ledger):
    try:
        folder = find_child(find_child(source_dir(2026), "8월"), "8월 운영비 영수증")
    except FileNotFoundError:
        pytest.skip("원본 영수증 없음")
    out = receipt_agent.extract_receipts([find_child(folder, "8월 운영비 - 얼맥당(8.16).pdf")], ledger)
    show("영수증", [out[0].call], f" | {out[0].날짜} {out[0].상호} {out[0].금액}")
    assert (out[0].날짜, out[0].금액) == (date(2026, 8, 16), 43_300)


def test_live_issue_unclear(ledger, book):
    r = issue_agent.assist_issue("I-019", ledger, book, "잘 모르겠어")
    show("이슈", r.calls, f" | {r.해결유형} 질문 {r.추가질문} | 설명문: {r.설명문}")
    assert r.해결유형 == "추가 확인 필요" and r.추가질문 and r.설명문
