"""Streamlit 앱 시나리오(AppTest). Claude는 FakeClient, 데이터는 임시 사본(BUDGET_DATA_DIR).
화면은 집행 보고서(영수증 업로드만)·설정 두 개."""
import io
import shutil
import zipfile
from datetime import date

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from core.paths import DATA_DIR, ROOT
from core.report import load_settings, save_settings
from tests.fakes import FakeClient, tool_response

APP = str(ROOT / "app" / "Home.py")
TODAY = date(2026, 11, 2)


@pytest.fixture
def data(tmp_path, monkeypatch):
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    monkeypatch.setenv("BUDGET_DATA_DIR", str(d))
    return d


def start(client=None) -> AppTest:
    """앱에는 로그인이 없다(접근 제한은 Streamlit 공유 설정). 열면 집행 보고서."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets["APP_ENV"] = "dev"
    at.session_state["_today"] = TODAY
    if client is not None:
        at.session_state["_llm_client"] = client
    return run_ok(at)


def run_ok(at):
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def go(at, page):
    at.switch_page(page)
    return run_ok(at)


def button(at, label):
    hits = [b for b in at.button if b.label == label]
    assert hits, f"버튼 없음: {label} / {[b.label for b in at.button]}"
    return hits[0]


def receipt(name, amount, day="2026-10-20"):
    return tool_response("submit_receipt", {
        "날짜": day, "상호": name, "금액": amount, "품목요약": "간식", "가맹점명_원문": name, "승인번호": "",
        "결제수단": "신용카드", "확인필요": False, "확인사유": ""})


def report_out(nos):
    return tool_response("submit_report_draft", {
        "머리말": "10월 교리 간식을 다음과 같이 사용하였습니다.",
        "경과보고_줄": [{"날짜": "10월 20일", "항목": "교리 간식", "영수증번호": nos, "참가자": [],
                      "노트_언급": False, "노트_날짜": None, "노트_금액": None}],
        "세부내역": [{"계정과목": "간식비", "내용": "교리 간식", "영수증번호": nos}],
        "불일치_메모": [], "대행사_상호": []})


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_only_report_and_settings_pages(data):
    at = start()
    assert at.title[0].value.startswith("🧾")
    assert [t.label for t in at.text_input] == ["행사"] and [t.label for t in at.text_area] == ["목적"]
    assert [d.key for d in at.date_input] == ["rep_시작일", "rep_종료일"]
    assert [t.key for t in at.time_input] == ["rep_시작시각", "rep_종료시각"]
    assert [n.label for n in at.number_input] == ["참가인원(총)", "남", "여"]  # 남·여는 '기타' 펼침 안
    assert not any(n.label in ("박", "일") for n in at.number_input)  # 박·일은 자동 계산
    for page in ("views/request.py", "views/dashboard.py", "views/issues.py", "views/post_settlement.py",
                 "views/year.py", "views/handover.py"):
        with pytest.raises(ValueError):  # 지운 화면은 등록돼 있지 않다
            at.switch_page(page)
    go(at, "views/settings.py")
    assert [t.label for t in at.tabs] == ["기본 정보·연락처", "Claude 모델·API 키"]


def test_receipts_only_to_report(data, monkeypatch):
    import hwpx.fill
    rendered = []
    real = hwpx.fill.render
    monkeypatch.setattr(hwpx.fill, "render", lambda doc, *a, **k: rendered.append(doc) or real(doc, *a, **k))
    s = load_settings(data)
    s.연락처.기안자, s.계좌.은행명, s.계좌.계좌번호, s.계좌.예금주 = "010-0000-0001", "테스트은행", "000-00-0000001", "홍길동"
    save_settings(s, data)
    before = {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}

    fake = FakeClient(receipt("편의점", 30_000), receipt("제과점", 25_000, "2026-10-21"), report_out([1, 2]))
    at = start(fake)
    assert button(at, "보고서 만들기").disabled  # 영수증 전
    at.text_input(key="rep_event").input("10월 초등부 교리 후 간식")
    at.text_area(key="rep_purpose").input("교리에 참여한 어린이 격려")
    at.date_input(key="rep_시작일").set_value(date(2026, 10, 20))
    at.date_input(key="rep_종료일").set_value(date(2026, 10, 21))
    at.number_input(key="rep_인원_총").set_value(40)
    at.number_input(key="rep_인원_남").set_value(18)
    run_ok(at)
    assert any("1박 2일" in c.value for c in at.caption)
    at.session_state["rep_files"] = [("a.png", png_bytes()), ("b.png", png_bytes())]
    run_ok(at)
    button(at, "보고서 만들기").click()
    run_ok(at)

    assert any("합계 55,000원" in x.value for x in at.success)
    prompt = fake.prompt()  # 행사·목적은 경과보고를 쓰는 맥락으로 Claude에 간다
    assert "행사: 10월 초등부 교리 후 간식" in prompt and "목적: 교리에 참여한 어린이 격려" in prompt
    assert "일시: 2026-10-20 10:00 ~ 2026-10-21 18:00" in prompt and "참가인원: 40명" in prompt
    assert "10월 | 부서 소풍(전례부, 성가대) | 전례부 30명×20, 성가대 15명×20" in prompt  # 사업계획 맥락
    doc = rendered[-1]
    assert doc.합계B == 55_000 and doc.경과보고 == "10월 교리 간식을 다음과 같이 사용하였습니다.\n10월 20일 교리 간식 : 55,000원"
    assert (doc.기안자, doc.주관단체, doc.기안자연락처, doc.은행명, doc.계좌번호, doc.예금주) == \
        ("정연재 레오", "초등부 교사회", "010-0000-0001", "테스트은행", "000-00-0000001", "홍길동")
    assert (doc.행사명, doc.목적) == ("10월 초등부 교리 후 간식", "교리에 참여한 어린이 격려")  # 입력 그대로
    assert (doc.시작.date(), doc.종료.date(), doc.박, doc.일) == (date(2026, 10, 20), date(2026, 10, 21), 1, 2)
    assert (doc.인원_총, doc.인원_남, doc.인원_여) == (40, 18, None)
    assert [(i.계정과목, i.내용, i.단가, i.수량, i.금액, i.비고) for i in doc.세부내역] == \
        [("간식비", "교리 간식", 55_000, 1, 55_000, "영수증 1, 2")]
    assert sum(i.금액 for i in doc.세부내역) == doc.합계B
    assert (doc.문서번호, doc.작성일, doc.본당보조, doc.잔액) == ("", None, None, None)
    with zipfile.ZipFile(io.BytesIO(at.session_state["rep_result"]["zip"])) as z:
        assert sorted(z.namelist()) == ["집행보고서_20261020.hwpx", "집행보고서_20261020_영수증첨부.docx"]
    assert before == {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}  # 아무것도 저장하지 않는다
    button(at, "다른 보고서 만들기").click()
    run_ok(at)
    assert button(at, "보고서 만들기").disabled


def test_settings_saved(data):
    at = go(start(), "views/settings.py")
    values = {"기안자 연락처": "010-0000-0001", "은행명": "테스트은행", "계좌번호": "000-00-0000001", "예금주": "홍길동"}
    for label, v in values.items():
        next(t for t in at.text_input if t.label == label).input(v)
    next(b for b in at.button if b.label == "저장").click()
    run_ok(at)
    s = load_settings(data)
    assert (s.연락처.기안자, s.계좌.계좌번호, s.계좌.예금주) == ("010-0000-0001", "000-00-0000001", "홍길동")
