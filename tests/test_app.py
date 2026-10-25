"""Streamlit 앱 시나리오(AppTest). Claude는 FakeClient, 데이터는 임시 사본(BUDGET_DATA_DIR).
화면은 집행 보고서·설정 두 개. 장부 없이 문서 정보를 직접 입력한다."""
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
        "경과보고_줄": [{"날짜": "10월 20일", "항목": "교리 간식", "영수증번호": nos, "참가자": ["정연재"],
                      "노트_언급": True, "노트_날짜": None, "노트_금액": None}],
        "세부내역": [{"계정과목": "간식비", "내용": "교리 간식", "영수증번호": nos}],
        "불일치_메모": [], "대행사_상호": []})


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_only_report_and_settings_pages(data):
    at = start()
    assert at.title[0].value.startswith("🧾")
    for page in ("views/request.py", "views/dashboard.py", "views/issues.py", "views/post_settlement.py",
                 "views/year.py", "views/handover.py"):
        with pytest.raises(ValueError):  # 지운 화면은 등록돼 있지 않다
            at.switch_page(page)
    go(at, "views/settings.py")
    assert [t.label for t in at.tabs] == ["기본 정보·연락처", "문서 양식", "Claude 모델·API 키"]
    labels = {w.label for w in at.number_input} | {w.label for w in at.text_input}
    assert not {"박", "일"} & labels


def test_report_flow_without_ledger(data, monkeypatch):
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
    assert not any(w.label in ("박", "일") for w in at.number_input)
    assert at.text_input(key="rep_no").value == "(집)2026-"  # 설정의 번호 앞부분
    at.text_input(key="rep_name").input("10월 교리 간식")
    at.number_input(key="rep_month").set_value(10)
    at.text_input(key="rep_no").input("(집)2026-060")
    at.text_input(key="rep_req_no").input("(예)2026-059")
    at.number_input(key="rep_applied").set_value(50_000)
    at.number_input(key="rep_인원_총").set_value(40)
    at.session_state["rep_files"] = [("a.png", png_bytes()), ("b.png", png_bytes())]
    at.text_area(key="rep_note").input("10/20 교리 간식 연재")
    run_ok(at)
    button(at, "영수증 읽기").click()
    run_ok(at)
    assert at.date_input(key="rep_시작일").value == date(2026, 10, 20)  # 비어 있던 날짜를 영수증으로
    assert at.date_input(key="rep_종료일").value == date(2026, 10, 21)
    button(at, "보고서 초안 만들기").click()
    run_ok(at)
    prompt = fake.prompt()
    assert "행사명: 10월 교리 간식" in prompt and "신청액: 50,000원" in prompt and "인원_총: 40" in prompt
    metrics = {m.label: m.value for m in at.metric}
    assert (metrics["합계(B) 실사용"], metrics["본당보조"], metrics["자체예산"], metrics["잔액(반납)"]) == \
        ("55,000원", "50,000원", "5,000원", "0원")
    assert "참가자 : 정연재 레오" in at.text_area(key="rep_body").value
    at.text_input(key="rep_holder").input("다른예금주")  # 이 문서만 예금주 덮어쓰기
    run_ok(at)
    button(at, "보고서 만들기").click()
    run_ok(at)

    assert any("10월 교리 간식 보고서를 만들었습니다" in s.value for s in at.success)
    assert any("2026/10월" in i.value for i in at.info)
    doc = rendered[-1]
    assert (doc.문서번호, doc.연결문서번호, doc.인원_총, doc.시작.date(), doc.종료.date()) == \
        ("(집)2026-060", "(예)2026-059", 40, date(2026, 10, 20), date(2026, 10, 21))
    assert (doc.기안자연락처, doc.은행명, doc.계좌번호, doc.예금주) == \
        ("010-0000-0001", "테스트은행", "000-00-0000001", "다른예금주")
    assert (doc.본당보조, doc.자체예산, doc.예산A, doc.잔액, doc.장소) == (50_000, 5_000, 55_000, 0, "개포동성당")
    with zipfile.ZipFile(io.BytesIO(at.session_state["rep_result"]["zip"])) as z:
        assert sorted(z.namelist()) == ["10월 교리 간식_보고서.hwpx", "10월 교리 간식_보고서_영수증첨부.docx"]
    assert before == {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}  # 보고서는 아무것도 저장하지 않는다


def test_report_blocks_without_name(data):
    at = start(FakeClient(receipt("편의점", 30_000), report_out([1])))
    at.session_state["rep_files"] = [("a.png", png_bytes())]
    run_ok(at)
    button(at, "영수증 읽기").click()
    run_ok(at)
    button(at, "보고서 초안 만들기").click()
    run_ok(at)
    assert any("행사명을 입력하세요" in e.value for e in at.error)
    assert button(at, "보고서 만들기").disabled


def test_settings_saved(data):
    at = go(start(), "views/settings.py")
    values = {"기안자 연락처": "010-0000-0001", "은행명": "테스트은행", "계좌번호": "000-00-0000001", "예금주": "홍길동"}
    for label, v in values.items():
        next(t for t in at.text_input if t.label == label).input(v)
    next(n for n in at.number_input if n.label == "회계연도").set_value(2027)
    next(b for b in at.button if b.label == "저장").click()
    run_ok(at)
    s = load_settings(data)
    assert (s.연락처.기안자, s.계좌.계좌번호, s.계좌.예금주, s.현재연도) == ("010-0000-0001", "000-00-0000001", "홍길동", 2027)
    at = go(start(), "views/report.py")
    assert at.text_input(key="rep_no").value == "(집)2027-"
