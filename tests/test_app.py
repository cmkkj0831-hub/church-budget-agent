"""Streamlit 앱 시나리오(AppTest). Claude는 FakeClient, 데이터는 임시 사본(BUDGET_DATA_DIR)."""
import io
import shutil
from datetime import date

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from core.issues import IssueBook
from core.ledger import Ledger, load_settings, read_csv, save_settings
from core.paths import DATA_DIR, ROOT
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
    """앱에는 로그인이 없다(접근 제한은 Streamlit 공유 설정). 열면 대시보드."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.secrets["APP_ENV"] = "dev"
    at.session_state["_today"] = TODAY
    if client is not None:
        at.session_state["_llm_client"] = client
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def button(at, label):
    hits = [b for b in at.button if b.label == label]
    assert hits, f"버튼 없음: {label} / {[b.label for b in at.button]}"
    return hits[0]


def checkbox(at, key):
    return next(c for c in at.checkbox if c.key == key)


def run_ok(at):
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def go(at, page):
    at.switch_page(page)
    return run_ok(at)


# ---------------------------------------------------------------- 화면·설정

def test_all_pages_open_without_login(data):
    at = start()
    assert at.title[0].value.startswith("📊") and not any("비밀번호" in t.label for t in at.text_input)
    top = " ".join(m.value for m in at.markdown)
    assert "2026 회계연도" in top and "문서 오류" in top and "예산 경고" in top and "증빙 누락·미확인" in top
    assert [t.label for t in at.tabs] == ["회계 현황", "사업계획서", "사용량"]
    assert not at.warning or not any("비밀번호" in w.value for w in at.warning)
    for page in ("views/request.py", "views/report.py", "views/post_settlement.py", "views/issues.py",
                 "views/settings.py", "views/year.py", "views/handover.py"):
        go(at, page)
    go(at, "views/dashboard.py")


def test_contacts_saved_plain_and_filled_into_documents(data, monkeypatch):
    import hwpx.fill
    rendered = []
    real = hwpx.fill.render
    monkeypatch.setattr(hwpx.fill, "render", lambda doc, *a, **k: rendered.append(doc) or real(doc, *a, **k))

    at = go(start(FakeClient(tool_response("submit_request_draft", CHRISTMAS))), "views/settings.py")
    values = {"기안자 연락처": "010-0000-0001", "기안단체장 연락처": "010-0000-0002",
              "은행명": "테스트은행", "계좌번호": "000-00-0000001", "예금주": "홍길동"}
    for label, v in values.items():
        next(t for t in at.text_input if t.label == label).input(v)
    next(b for b in at.button if b.label == "저장").click()
    run_ok(at)
    s = load_settings(data)
    assert (s.연락처.기안자, s.계좌.계좌번호, s.계좌.예금주) == ("010-0000-0001", "000-00-0000001", "홍길동")
    assert not (data / "settings_private.enc").exists()

    go(at, "views/request.py")
    at.text_area(key="req_text").input("12월 20일 성탄 행사")
    run_ok(at)
    button(at, "초안 만들기").click()
    run_ok(at)
    at.text_input(key="req_holder").input("다른예금주")  # 이 문서만 예금주 덮어쓰기
    run_ok(at)
    confirm_and_click(at, "req_confirm", "req_accept", "신청서 생성·장부 반영")
    doc = rendered[-1]
    assert (doc.기안자연락처, doc.기안단체장연락처, doc.은행명, doc.계좌번호, doc.예금주) == \
        ("010-0000-0001", "010-0000-0002", "테스트은행", "000-00-0000001", "다른예금주")
    assert "다른예금주" not in (data / "settings.json").read_text(encoding="utf-8")  # 덮어쓰기는 문서에만
    assert "010-0000-0001" not in (data / "2026" / "ledger.csv").read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------- 신청 → 보고 → 사후정산 → 대시보드

CHRISTMAS = {
    "행사명": "성탄 행사", "대상월": 12, "행사유형": "행사·교육·피정", "목적": "성탄 축하",
    "시작일": "2026-12-20", "시작시각": "", "종료일": "", "종료시각": "", "박": None, "일": None,
    "장소": "", "참가대상": "초등부 주일학교", "인원_총": 120, "인원_남": 60, "인원_여": 60,
    "세부항목": [{"계정과목": "간식비", "내용": "간식", "단가": 3000, "수량": 120},
              {"계정과목": "선물비", "내용": "선물", "단가": 5000, "수량": 120}],
    "사업ID_후보": [{"사업ID": "P10", "근거": "성탄절 행사"}], "행사일정": "12월 20일 성탄 미사 후 행사", "missing": [],
}


def receipt(name, amount, day="2026-12-20"):
    return tool_response("submit_receipt", {
        "날짜": day, "상호": name, "금액": amount, "품목요약": "간식", "가맹점명_원문": name, "승인번호": "",
        "결제수단": "신용카드", "확인필요": False, "확인사유": ""})


def report_out(nos, month=12, name="성탄 행사"):
    return tool_response("submit_report_draft", {
        "머리말": f"{name} 예산을 다음과 같이 사용하였습니다.",
        "경과보고_줄": [{"날짜": f"{month}월 20일", "항목": "행사 간식", "영수증번호": nos, "참가자": ["정연재"],
                      "노트_언급": True, "노트_날짜": None, "노트_금액": None}],
        "세부내역": [{"계정과목": "간식비", "내용": "행사 간식", "영수증번호": nos}],
        "건의사항": "", "불일치_메모": [], "행사명_제안": name, "대상월": month,
        "사업ID_후보": [{"사업ID": "P10", "근거": "성탄"}], "대행사_상호": []})


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (60, 80), "white").save(buf, format="PNG")
    return buf.getvalue()


def confirm_and_click(at, confirm_key, accept_key, label):
    checkbox(at, confirm_key).check()
    run_ok(at)
    if any(c.key == accept_key for c in at.checkbox):
        checkbox(at, accept_key).check()
        run_ok(at)
    button(at, label).click()
    return run_ok(at)


def test_request_report_post_settlement_flow(data):
    fake = FakeClient(tool_response("submit_request_draft", CHRISTMAS),
                      receipt("편의점", 300_000), receipt("문구점", 450_000), report_out([1, 2]),
                      receipt("제과점", 200_000, "2026-11-01"), report_out([1], 11, "11월 교리 간식"))
    at = start(fake)

    go(at, "views/request.py")
    at.text_area(key="req_text").input("12월 20일 성탄 행사, 어린이 120명, 간식 3000원씩, 선물 5000원씩")
    run_ok(at)
    button(at, "초안 만들기").click()
    run_ok(at)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["합계(금번 신청액)"] == "960,000원" and metrics["문서번호"] == "(예)2026-053"
    assert button(at, "신청서 생성·장부 반영").disabled  # 미리보기 확인 전
    confirm_and_click(at, "req_confirm", "req_accept", "신청서 생성·장부 반영")
    assert any("(예)2026-053 성탄 행사 신청서" in s.value for s in at.success)
    assert any("2026/12월" in i.value and "1_성탄 행사_신청서.hwpx" in i.value for i in at.info)
    assert not any(b.label == "초안 만들기" for b in at.button) and at.get("download_button")
    L = Ledger.load(data)
    assert (L.row(54).문서번호, L.row(54).금번신청액, L.row(54).상태) == ("(예)2026-053", 960_000, "진행중")
    usage = read_csv(data / "2026" / "usage.csv")
    assert usage[-1]["기능"] == "신청서 초안" and usage[-1]["환경"] == "dev"

    go(at, "views/report.py")
    at.selectbox(key="rep_seq").select(54)
    at.session_state["rep_files"] = [("a.png", png_bytes()), ("b.png", png_bytes())]
    at.text_area(key="rep_note").input("12/20 행사 간식 연재")
    run_ok(at)
    button(at, "영수증 읽기").click()
    run_ok(at)
    button(at, "보고서 초안 만들기").click()
    run_ok(at)
    metrics = {m.label: m.value for m in at.metric}
    assert (metrics["합계(B) 실사용"], metrics["본당보조"], metrics["잔액(반납)"]) == ("750,000원", "750,000원", "210,000원")
    assert "참가자 : 정연재 레오" in at.text_area(key="rep_body").value
    assert not any("건의" in t.label or "차기" in t.label for t in at.text_area)  # 건의·차기계획 칸 없음
    confirm_and_click(at, "rep_confirm", "rep_accept", "보고서 생성·장부 반영")
    assert any("(집)2026-054" in s.value for s in at.success)
    L = Ledger.load(data)
    assert L.row(54).연결문서번호 == "(집)2026-054" and L.row(55).잔액 == 210_000

    go(at, "views/post_settlement.py")
    at.selectbox(key="ps_pid").select("P12")
    at.number_input(key="ps_month").set_value(11)
    at.text_input(key="ps_name").input("11월 교리 간식")
    at.session_state["ps_files"] = [("c.png", png_bytes())]
    at.text_area(key="ps_note").input("11/1 교리 간식 20만원")
    run_ok(at)
    assert not any("건의" in t.label or "차기" in t.label for t in at.text_area)  # 건의·차기계획 칸 없음
    button(at, "영수증 읽고 보고서 초안 만들기").click()
    run_ok(at)
    prompt = fake.prompt()
    assert "[사후정산 사업·행사" in prompt and "사업: P12 " in prompt and "대상 월: 11월" in prompt
    assert "행사명: 11월 교리 간식" in prompt and "[연결 신청서]" not in prompt
    assert not any("건의" in t.label for t in at.text_area)
    assert "309,855원" in at.table[0].value.to_string()
    confirm_and_click(at, "ps_confirm", "ps_accept", "신청서·보고서 생성·장부 반영")
    assert any("사후정산 (예)2026-055" in s.value for s in at.success)
    L = Ledger.load(data)
    assert (L.row(56).문서번호, L.row(57).문서번호, L.row(56).금번신청액) == ("(예)2026-055", "(집)2026-056", 200_000)

    go(at, "views/dashboard.py")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["신청 누계(재계산)"] == f"{23_694_010 + 750_000 + 200_000:,}원"
    assert len(fake.responses) == 0
    assert len(read_csv(data / "2026" / "usage.csv")) == 6


# ---------------------------------------------------------------- 부서 운영비 이슈: 설명 → 분석 → 반영 → 되돌리기

PLAN = {"해결유형": "설명", "변경안": [{"순번": 38, "열": "상태", "변경후": "취소", "사유": "철회"}], "추가행": [],
        "함께해결": ["I-018"], "해결내용": "부서 운영비 신청 철회, 지급 없음. 상태 취소.", "남은의문": [], "추가질문": [],
        "설명문": "{대상문서}는 철회되어 지급되지 않았습니다. 취소하면 잔여 예산이 늘어납니다."}


def test_issue_resolve_then_undo(data):
    at = go(start(FakeClient(tool_response("submit_issue_assessment", PLAN))), "views/issues.py")
    at.selectbox(key="iss_id").select("I-019")
    run_ok(at)
    at.text_area(key="iss_text_I-019").input("부서 운영비는 철회됐어. 지급받지 않았어.")
    run_ok(at)
    button(at, "분석").click()
    run_ok(at)
    checkbox(at, "iss_ok_I-019").check()
    run_ok(at)
    button(at, "반영").click()
    run_ok(at)
    assert any("I-019 해결(설명됨)" in s.value for s in at.success)
    L, B = Ledger.load(data), IssueBook.load(data)
    assert L.row(38).상태 == "취소" and B.get("I-019").상태 == B.get("I-018").상태 == "해결-설명됨"
    assert B.get("I-019").설명 not in B.ledger_remarks().get(38, "")
    unit = B.history[-1].이력ID

    at.selectbox(key="iss_undo_pick").select(unit)
    run_ok(at)
    checkbox(at, "iss_undo_ok").check()
    run_ok(at)
    button(at, "되돌리기").click()
    run_ok(at)
    assert any(f"{unit}을(를) 되돌렸습니다" in s.value for s in at.success)
    L, B = Ledger.load(data), IssueBook.load(data)
    assert L.row(38).상태 == "보고서없음" and B.get("I-019").상태 == "미해결"
    assert B.get("I-019").설명 in B.ledger_remarks()[38]
    assert B.history[-1].사유 == f"되돌리기: {unit}"


def test_delete_document_then_undo(data):
    at = go(start(FakeClient(tool_response("submit_request_draft", CHRISTMAS))), "views/request.py")
    at.text_area(key="req_text").input("12월 20일 성탄 행사")
    run_ok(at)
    button(at, "초안 만들기").click()
    run_ok(at)
    confirm_and_click(at, "req_confirm", "req_accept", "신청서 생성·장부 반영")
    assert len(Ledger.load(data).rows) == 54

    go(at, "views/issues.py")
    at.multiselect(key="del_pick").select(54)
    run_ok(at)
    assert "(예)2026-053" in at.dataframe[-1].value.to_string()
    assert button(at, "문서 삭제").disabled  # 사유·확인 전
    at.text_input(key="del_reason").input("금액 잘못 입력")
    checkbox(at, "del_ok").check()
    run_ok(at)
    button(at, "문서 삭제").click()
    run_ok(at)
    assert any("(예)2026-053을(를) 장부에서 지웠습니다" in s.value for s in at.success)
    L, B = Ledger.load(data), IssueBook.load(data)
    assert len(L.rows) == 53 and L.next_doc_number() == 53
    units = {h.이력ID: h.사유 for h in B.history}
    unit = next(k for k, v in units.items() if v.startswith("장부: (예)2026-053 성탄 행사 삭제(금액 잘못 입력)"))

    at.selectbox(key="iss_undo_pick").select(unit)
    checkbox(at, "iss_undo_ok").check()
    run_ok(at)
    button(at, "되돌리기").click()
    run_ok(at)
    assert Ledger.load(data).row(54).문서번호 == "(예)2026-053"


def test_delete_tab_refuses_original_rows(data):
    at = go(start(), "views/issues.py")
    assert any("지울 수 있는 문서(앱에서 만든 문서)가 없습니다" in i.value for i in at.info)


def test_rescan_registers_selected(data):
    L = Ledger.load(data)
    L.row(53).합계 = None  # 새 이상: 합계란 공란
    L.save()
    at = go(start(), "views/issues.py")
    button(at, "장부 재검사").click()
    run_ok(at)
    at.session_state["iss_scan_table"] = {"edited_rows": {0: {"등록": True}}, "added_rows": [], "deleted_rows": []}
    run_ok(at)
    button(at, "선택한 1건 이슈 등록").click()
    run_ok(at)
    B = IssueBook.load(data)
    assert B.issues[-1].대상순번 == [53] and B.issues[-1].근거.startswith("장부 재검사")


# ---------------------------------------------------------------- 2027 회계연도 전환 마법사

def test_year_wizard(data):
    at = go(start(), "views/year.py")
    button(at, "전환 시작").click()
    run_ok(at)
    assert at.get("download_button")  # ① 연간 결산
    checkbox(at, "yr_close_ok").check()
    run_ok(at)
    button(at, "마감하고 다음").click()
    run_ok(at)
    assert load_settings(data).year_info(2026).마감

    ids = [i.이슈ID for i in IssueBook.load(data, 2026).open_issues()]  # ② I-019만 이월
    at.session_state["yr_carry"] = {"edited_rows": {ids.index("I-019"): {"이월": True}}, "added_rows": [],
                                    "deleted_rows": []}
    run_ok(at)
    button(at, "1건 이월하고 다음").click()
    run_ok(at)

    at.radio(key="yr_how").set_value("지난해 계획 복사 후 고치기")  # ③
    run_ok(at)
    button(at, "지난해 계획 불러오기").click()
    run_ok(at)
    assert any("검증 통과" in s.value for s in at.success)
    button(at, "사업계획서 확정").click()
    run_ok(at)

    checkbox(at, "yr_create_ok").check()  # ④
    run_ok(at)
    button(at, "새 장부 생성").click()
    run_ok(at)
    s = load_settings(data)
    assert s.현재연도 == 2027 and s.year_info(2027).예산배정액 == 36_470_000
    L, B = Ledger.load(data), IssueBook.load(data)
    assert (L.year, len(L.rows), L.next_doc_number()) == (2027, 0, 1)
    assert len(B.issues) == 1 and B.issues[0].설명.startswith("[2026 이월] ")

    button(at, "바꿀 것 없음 — 다음").click()  # ⑤
    run_ok(at)
    button(at, "회계연도 전환 마치기").click()  # ⑥
    run_ok(at)
    assert "연도전환" not in load_settings(data).진행상태
    go(at, "views/request.py")
    assert not any("읽기 전용" in i.value for i in at.info)
    assert "2027 회계연도" in " ".join(m.value for m in at.markdown)


def test_handover_package(data):
    import json
    import zipfile
    s = load_settings(data)
    s.계좌.계좌번호, s.연락처.기안자 = "000-00-0000009", "010-0000-0009"
    save_settings(s, data)
    at = go(start(), "views/handover.py")
    assert any("공유 설정" in c.label and "이전 담당자를 제거" in c.label for c in at.checkbox)
    assert not any("비밀번호" in c.label for c in at.checkbox)
    button(at, "인수인계 패키지 만들기").click()
    run_ok(at)
    files = at.session_state["handover_files"]
    assert {"사용설명서.md", "회계담당_접속안내.md", "2026_회계내역.xlsx", "settings(연락처·계좌 제외).json"} <= set(files)
    with zipfile.ZipFile(io.BytesIO(at.session_state["handover_zip"])) as z:
        shared = json.loads(z.read("settings(연락처·계좌 제외).json"))
    assert shared["계좌"]["계좌번호"] == shared["연락처"]["기안자"] == "" and shared["교사명단"]
