"""에이전트 단위 테스트. Claude API는 tests/fakes.FakeClient로 대신한다(실제 호출은 test_agents_live.py, --live)."""
import base64
import io
from datetime import date, datetime

import pytest
from PIL import Image

from agents import issue_agent, plan_agent, post_settlement, receipt_agent, report_agent, request_agent
from agents.llm import FALLBACK_BETA, KEY_PATTERN, MAX_UNION_PARAMS, AgentError, AgentRefusal, call_tool, unalias
from agents.receipt_agent import ReceiptExtraction
from core.models import ReportDoc
from hwpx.fill import render
from hwpx.reader import read_document
from tests.conftest import TODAY
from types import SimpleNamespace

from tests.fakes import FakeClient, text_response, tool_response, usage

ALL_SPECS = [request_agent.SPEC, receipt_agent.SPEC, report_agent.SPEC, issue_agent.SPEC,
             issue_agent.EXPLAIN_SPEC, plan_agent.SPEC]


# ---------------------------------------------------------------- 공통 호출

def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object" or (isinstance(schema.get("type"), list) and "object" in schema["type"]):
            yield schema
        for v in schema.values():
            yield from _objects(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _objects(v)


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s["name"])
def test_specs_are_strict(spec):
    assert spec["strict"] is True
    for o in _objects(spec["input_schema"]):
        assert o["additionalProperties"] is False
        assert set(o["required"]) == set(o["properties"])
        # API는 속성 이름으로 ^[a-zA-Z0-9_.-]{1,64}$만 받는다(한글 키 400)
        assert all(KEY_PATTERN.match(k) for k in o["properties"]), list(o["properties"])
    unions = sum(1 for o in _objects(spec["input_schema"]) for p in o["properties"].values()
                 if isinstance(p.get("type"), list) or "anyOf" in p)
    assert unions <= MAX_UNION_PARAMS, unions


def test_aliases_round_trip():
    payload = {"event_name": "성탄", "items": [{"account": "간식비", "unit_price": 3000}], "unknown": 1}
    assert unalias(payload) == {"행사명": "성탄", "세부항목": [{"계정과목": "간식비", "단가": 3000}], "unknown": 1}


def test_call_tool_request_shape_and_retry(ledger):
    fake = FakeClient(text_response("먼저 설명드리면..."),
                      tool_response("submit_receipt", {"x": 1}, usage=usage(1200, 80)))
    out = call_tool(fake, "claude-sonnet-5-5", "시스템", "내용", receipt_agent.SPEC)
    assert out.input == {"x": 1} and len(fake.calls) == 2
    first = fake.calls[0]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["tool_choice"] == {"type": "auto"}  # Sonnet 5.5는 강제 tool_choice 불가
    assert first["tools"][0]["strict"] is True
    assert "betas" not in first and "fallbacks" not in first and "_beta" not in first  # 기본: fallbacks 끔
    assert "submit_receipt" in first["system"]
    assert fake.calls[1]["messages"][-1]["role"] == "user"  # 도구 호출을 다시 요청
    info = out.info
    assert (info.model, info.served_by_fallback, info.attempts) == ("claude-sonnet-5-5", False, 2)
    assert (info.input_tokens, info.output_tokens) == (1200, 80) and info.seconds >= 0


def test_fallbacks_option_and_served_model(ledger):
    ledger.settings.llm_fallbacks = "default"
    it = [SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]
    fake = FakeClient(tool_response("submit_request_draft", _christmas_output(), model="claude-opus-4-8",
                                    usage=usage(100, 10, it), fallback_from="claude-sonnet-5-5"))
    d = request_agent.extract_request("x", ledger, TODAY, client=fake)
    c = fake.calls[0]
    assert c["_beta"] and c["betas"] == [FALLBACK_BETA] and c["fallbacks"] == "default"
    info = d.calls[0]
    assert (info.requested_model, info.model, info.served_by_fallback) == \
        ("claude-sonnet-5-5", "claude-opus-4-8", True)
    assert "대체 모델" in info.line()


def test_call_tool_refusal_and_no_tool():
    with pytest.raises(AgentRefusal):
        call_tool(FakeClient(text_response("", stop_reason="refusal")), "m", "s", "c", receipt_agent.SPEC)
    with pytest.raises(AgentError):
        call_tool(FakeClient(text_response("a"), text_response("b")), "m", "s", "c", receipt_agent.SPEC)


def test_model_comes_from_settings(ledger):
    fake = FakeClient(tool_response("submit_request_draft", _christmas_output()))
    request_agent.extract_request("x", ledger, TODAY, client=fake)
    assert fake.calls[0]["model"] == ledger.settings.모델 == "claude-sonnet-5-5"


# ---------------------------------------------------------------- 1. 신청서

def _christmas_output(**over):
    out = {
        "행사명": "성탄 행사", "대상월": 12, "행사유형": "행사·교육·피정", "목적": "",
        "시작일": "2026-12-20", "시작시각": "", "종료일": "", "종료시각": "", "박": None, "일": None,
        "장소": "", "참가대상": "초등부 주일학교", "인원_총": 120, "인원_남": None, "인원_여": None,
        "세부항목": [{"계정과목": "간식비", "내용": "간식", "단가": 3000, "수량": 120},
                  {"계정과목": "선물비", "내용": "선물", "단가": 5000, "수량": 120}],
        "사업ID_후보": [{"사업ID": "P10", "근거": "성탄절 행사"}, {"사업ID": "P99", "근거": "없는 사업"}],
        "행사일정": "12월 20일 성탄 미사 후 성탄 행사를 진행합니다.",
        "missing": ["목적"],
    }
    out.update(over)
    return out


def test_request_draft_christmas(ledger):
    fake = FakeClient(tool_response("submit_request_draft", _christmas_output()))
    d = request_agent.extract_request("12월 20일 성탄 행사, 어린이 120명, 간식 3000원씩, 선물 5000원씩",
                                      ledger, TODAY, client=fake)
    assert "P10 | 성탄절 행사" in fake.prompt() and "2026년" in fake.prompt()
    doc = d.doc
    assert (doc.월, doc.사업ID, doc.행사명) == (12, "P10", "성탄 행사")
    assert [c["사업ID"] for c in d.candidates] == ["P10"]  # 계획에 없는 후보는 버림
    assert doc.시작 == datetime(2026, 12, 20, 10) and doc.종료 == datetime(2026, 12, 20, 18)
    assert doc.장소 == "개포동성당"
    assert set(d.defaulted) == {"시작시각", "종료시각", "장소"}
    assert {"목적", "인원_남", "인원_여"} <= set(d.missing) and "행사명" not in d.missing
    assert all(i.금액 is None for i in doc.세부내역)  # 금액은 LLM이 아니라 core가
    built = ledger.build_request(doc, today=TODAY).docs[0]
    assert built.합계 == 3000 * 120 + 5000 * 120 == built.금번신청액


def test_request_draft_missing_quantities(ledger):
    out = _christmas_output(세부항목=[{"계정과목": "선물비", "내용": "선물", "단가": 5000, "수량": None}],
                            시작일="", 대상월=None, 사업ID_후보=[])
    d = request_agent.to_draft(out, ledger)
    assert d.doc.세부내역 == [] and d.doc.시작 is None
    assert {"세부항목1(선물) 수량", "시작일", "대상월", "사업ID"} <= set(d.missing)
    assert "시작시각" not in d.defaulted  # 날짜가 없으면 시각도 채우지 않는다


# ---------------------------------------------------------------- 2. 영수증

def test_receipts_rotation_and_unreadable_amount(ledger, tmp_path):
    img = Image.new("RGB", (400, 200), "white")
    img.paste(Image.new("RGB", (300, 120), "black"), (50, 40))
    exif = Image.Exif()
    exif[0x0112] = 6
    img.save(tmp_path / "rot.jpg", exif=exif)
    Image.new("RGB", (120, 160), "gray").save(tmp_path / "b.png")
    fake = FakeClient(
        tool_response("submit_receipt", {"날짜": "2026-08-15", "상호": "우리생고기", "금액": 281000,
                                         "품목요약": "식사", "확인필요": False, "확인사유": ""}),
        tool_response("submit_receipt", {"날짜": "2026-08-16", "상호": "편의점", "금액": None,
                                         "품목요약": "간식", "확인필요": False, "확인사유": ""}),
    )
    out = receipt_agent.extract_receipts([tmp_path / "rot.jpg", tmp_path / "b.png"], ledger, client=fake)
    sent = fake.calls[0]["messages"][0]["content"][0]["source"]
    shown = Image.open(io.BytesIO(base64.b64decode(sent["data"])))
    assert sent["media_type"] == "image/jpeg" and shown.height > shown.width  # EXIF 회전 반영
    assert (out[0].금액, out[0].확인필요, out[0].날짜) == (281000, False, date(2026, 8, 15))
    assert out[1].금액 is None and out[1].확인필요 and "금액을 읽지 못함" in out[1].확인사유
    assert out[0].to_receipt().용도 == "식사"


def test_receipts_heic(ledger, tmp_path):
    pytest.importorskip("pillow_heif")
    try:
        Image.new("RGB", (300, 200), "white").save(tmp_path / "a.heic")
    except Exception:
        pytest.skip("HEIC 저장 불가 환경")
    fake = FakeClient(tool_response("submit_receipt", {"날짜": "2026-08-15", "상호": "가게", "금액": 1000,
                                                       "품목요약": "간식", "확인필요": False, "확인사유": ""}))
    assert receipt_agent.extract_receipts([tmp_path / "a.heic"], ledger, client=fake)[0].금액 == 1000


# ---------------------------------------------------------------- 3. 보고서

RECEIPTS = [
    ReceiptExtraction(1, "a.pdf", date(2026, 8, 15), "우리생고기", 281_000, "식사", False),
    ReceiptExtraction(2, "b.pdf", date(2026, 8, 15), "컴포즈커피", 11_100, "커피", False),
    ReceiptExtraction(3, "c.pdf", date(2026, 8, 16), "얼맥당", 43_300, "식사", False),
    ReceiptExtraction(4, "d.jpg", date(2026, 8, 20), "다이소", None, "준비물", True, "금액을 읽지 못함"),
]


def _report_output(**over):
    out = {
        "머리말": "8월 교사회 운영비를 다음과 같이 사용하였습니다.",
        "경과보고_줄": [
            {"날짜": "8월 15일", "항목": "미사 후 식대", "영수증번호": [1, 2], "참가자": ["정연재", "플로라", "김철수"],
             "노트_언급": True, "노트_날짜": "8월 15일", "노트_금액": 290000},
            {"날짜": "8월 16일", "항목": "회합 후 식대", "영수증번호": [3], "참가자": ["정연재 레오"],
             "노트_언급": True, "노트_날짜": "8월 14일", "노트_금액": 43300},
        ],
        "세부내역": [{"계정과목": "식대", "내용": "미사 후 식대", "영수증번호": [1, 3]},
                  {"계정과목": "다과비", "내용": "커피", "영수증번호": [2, 3]}],
        "건의사항": "다음 달부터 교사 인원 증가로 운영비 증액이 필요합니다.",
        "불일치_메모": [],
        "행사명_제안": "8월 교사회 운영비", "대상월": 8, "사업ID_후보": [{"사업ID": "P11", "근거": "운영비"}],
    }
    out.update(over)
    return out


def test_report_draft_with_note(ledger):
    fake = FakeClient(tool_response("submit_report_draft", _report_output()))
    note = "8/15 미사 후 식대 29만원 연재, 플로라, 김철수 / 8/14 회합 후 식대 43,300원 연재"
    d = report_agent.draft_report(ledger.row(40), RECEIPTS, ledger, progress_note=note,
                                  suggestion="운영비 올려주세요", 작성일=date(2026, 8, 31), client=fake, today=TODAY)
    lines = d.composition.경과보고.split("\n")
    assert lines[0] == "8월 교사회 운영비를 다음과 같이 사용하였습니다."
    assert lines[1] == "8월 15일 미사 후 식대 : 292,100원 / 참가자 : 정연재 레오, 정지연 플로라, 김철수"
    assert lines[2] == "8월 16일 회합 후 식대 : 43,300원 / 참가자 : 정연재 레오"
    # 세부내역 금액은 영수증 합(LLM이 아님). 3번이 두 곳에 들어가 한 번만, 4번은 따로
    assert [(i.계정과목, i.단가) for i in d.doc.세부내역] == [("식대", 324_300), ("다과비", 11_100), ("기타", 0)]
    w = "\n".join(d.warnings)
    for expected in ("명단에 없는 참가자: '김철수'", "금액 불일치: '8월 15일 미사 후 식대' 노트 290,000원 / 영수증 292,100원",
                     "날짜 불일치: '회합 후 식대' 노트 8월 14일 / 영수증 8월 16일", "progress_note에 없는 영수증: 4번 다이소",
                     "영수증 3번이 세부내역 두 곳", "영수증 4번(다이소) 확인 필요"):
        assert expected in w, expected
    # 보고서 금액은 core 규칙: 신청 540,000, 실사용 335,400 → 반납 204,600
    assert (d.doc.본당보조, d.doc.자체예산, d.doc.합계B, d.doc.잔액) == (335_400, 0, 335_400, 204_600)
    assert d.doc.건의사항.startswith("다음 달부터")
    assert report_agent.FORMAT_EXAMPLE.split("\n")[1] in fake.prompt()


def test_report_draft_without_note(ledger):
    out = _report_output(경과보고_줄=[{"날짜": None, "항목": "식대", "영수증번호": [1], "참가자": [],
                                    "노트_언급": False, "노트_날짜": None, "노트_금액": None}])
    fake = FakeClient(tool_response("submit_report_draft", out))
    d = report_agent.draft_report(ledger.row(40), RECEIPTS[:3], ledger, client=fake, today=TODAY)
    assert d.composition.lines[0].text() == "8월 15일 식대 : 281,000원"
    assert not any("progress_note" in x for x in d.warnings)
    assert d.doc.건의사항 == ""  # suggestion 없으면 공란


def test_participant_matching():
    roster = ["정연재 레오", "양준형 사도요한", "조민기 사도요한", "정지연 플로라"]
    m = report_agent.match_participant
    assert m("정연재", roster) == "정연재 레오" and m("플로라", roster) == "정지연 플로라"
    assert m("사도요한", roster) is None  # 세례명이 둘이면 판단하지 않는다
    assert report_agent.participant_candidates("사도요한", roster) == ["양준형 사도요한", "조민기 사도요한"]
    assert m("정연재 바오로", roster) is None and report_agent.participant_candidates("정연재 바오로", roster) == []


def test_ambiguous_participant_returns_candidates(ledger):
    out = _report_output(경과보고_줄=[{"날짜": "8월 15일", "항목": "미사 후 식대", "영수증번호": [1, 2, 3],
                                    "참가자": ["사도요한", "정연재"], "노트_언급": False, "노트_날짜": None,
                                    "노트_금액": None}])
    d = report_agent.draft_report(ledger.row(40), RECEIPTS[:3], ledger,
                                  client=FakeClient(tool_response("submit_report_draft", out)), today=TODAY)
    assert d.composition.참가자_선택 == [{"줄": "8월 15일 미사 후 식대", "입력": "사도요한",
                                       "후보": ["양준형 사도요한", "조민기 사도요한"]}]
    assert any("참가자 확인 필요: '사도요한' → 후보 양준형 사도요한, 조민기 사도요한" in w for w in d.warnings)
    assert "참가자 : 사도요한, 정연재 레오" in d.composition.경과보고  # 고르기 전에는 입력 그대로


# ---------------------------------------------------------------- 4. 사후정산

def test_post_settlement_flow(ledger):
    receipts = [ReceiptExtraction(1, "a.jpg", date(2026, 10, 24), "교보문고", 350_000, "교재", False)]
    out = _report_output(
        머리말="10월 교리 준비비를 다음과 같이 사용하였습니다.",
        경과보고_줄=[{"날짜": "10월 24일", "항목": "교리 교재 구입", "영수증번호": [1], "참가자": [],
                    "노트_언급": True, "노트_날짜": "10월 24일", "노트_금액": 350000}],
        세부내역=[{"계정과목": "교재비", "내용": "교리 교재", "영수증번호": [1]}],
        행사명_제안="10월 교리 준비비", 대상월=10, 사업ID_후보=[{"사업ID": "P12", "근거": "교리"}])
    fake = FakeClient(tool_response("submit_report_draft", out))
    r = post_settlement.draft_post_settlement(receipts, "10월 24일 교리 교재 35만원", ledger,
                                              작성일=date(2026, 10, 24), client=fake, today=TODAY)
    req, rep = r.build.docs
    assert (req.금번신청액, rep.본당보조, rep.자체예산) == (309_855, 309_855, 40_145)
    assert (req.문서번호, rep.문서번호, req.작성일) == ("(예)2026-053", "(집)2026-054", rep.작성일)
    assert any("LLM 제안" in w for w in r.warnings) and r.missing == []
    assert "사후정산" in fake.prompt()


def test_post_settlement_sends_chosen_business(ledger):
    out = _report_output(사업ID_후보=[{"사업ID": "P12", "근거": "교리"}], 대상월=10, 행사명_제안="10월 교리 준비비")
    fake = FakeClient(tool_response("submit_report_draft", out))
    post_settlement.draft_post_settlement(RECEIPTS[:1], None, ledger, 사업ID="P12", 월=10, 행사명="10월 교리 준비비",
                                          client=fake, today=TODAY)
    prompt = fake.prompt()
    p = ledger.plan["P12"]
    assert f"사업: P12 {p.사업명}" in prompt and "대상 월: 10월" in prompt and "행사명: 10월 교리 준비비" in prompt
    assert "[연결 신청서]" not in prompt and "후보를 제안하라" not in prompt


def test_post_settlement_missing_business(ledger):
    out = _report_output(사업ID_후보=[], 대상월=None, 행사명_제안=None)
    r = post_settlement.draft_post_settlement(RECEIPTS[:1], None, ledger,
                                              client=FakeClient(tool_response("submit_report_draft", out)))
    assert r.build is None and set(r.missing) == {"사업ID", "대상월", "행사명"}


# ---------------------------------------------------------------- 5. 이슈

def _issue_out(kind, changes=(), rows=(), together=(), questions=()):
    return {"해결유형": kind, "변경안": list(changes), "추가행": list(rows), "함께해결": list(together),
            "해결내용": "처리 내용", "남은의문": [], "추가질문": list(questions),
            "설명문": "{대상문서}에 대한 집행보고서가 없고 {대상금액}이 누계에 빠져 있습니다. "
                   "지급 여부에 따라 잔여 예산이 달라지며 재계산 잔여는 {잔여액}입니다. 지급 여부를 확인해 주세요."}


def test_issue_withdrawn_is_explanation(ledger, book):
    out = _issue_out("설명", [{"순번": 38, "열": "상태", "변경후": "취소", "사유": "철회"}], together=["I-018"])
    fake = FakeClient(tool_response("submit_issue_assessment", out))
    r = issue_agent.assist_issue("I-019", ledger, book, "부서 운영비는 철회됐어", client=fake)
    assert r.해결유형 == "설명" and r.resolution.방식 == "설명" and r.resolution.함께해결 == ["I-018"]
    v = r.verification
    assert v.누계_후 == v.누계_전 - 480_000
    assert r.diff == [{"순번": 38, "열": "상태", "변경전": "보고서없음", "변경후": "취소"}]
    assert ledger.row(38).상태 == "보고서없음" and book.get("I-019").상태 == "미해결"  # 원본 그대로
    p = fake.prompt()
    assert "부서 운영비는 철회됐어" in p and "23694010" in p and '"대상순번별_누계_단절": {"38": -480000}' in p


def test_issue_attachment_correction(ledger, book, tmp_path):
    """I-014(순번 36 잔액란 공란)에 잔액 0원이 적힌 보고서 hwpx를 첨부 → 정정."""
    row = ledger.row(36)
    doc = ReportDoc(월=6, 사업ID="P12", 행사명=row.행사명, 문서번호=row.문서번호, 작성일=date(2026, 6, 28),
                    자체예산=0, 본당보조=259_300, 예산A=259_300, 합계B=259_300, 잔액=0)
    att = read_document(render(doc, tmp_path / "att.hwpx"))
    out = _issue_out("정정", [{"순번": 36, "열": "잔액", "변경후": "0", "사유": "첨부 보고서 잔액 0원"}])
    fake = FakeClient(tool_response("submit_issue_assessment", out))
    r = issue_agent.assist_issue("I-014", ledger, book, "원본 보고서 다시 보니 잔액 0원이야", attachment=att, client=fake)
    assert r.첨부대조 == [{"순번": 36, "열": "잔액", "장부": None, "첨부": 0}]
    assert '"열": "잔액", "장부": null, "첨부": 0' in fake.prompt()
    assert r.해결유형 == "정정" and r.diff == [{"순번": 36, "열": "잔액", "변경전": None, "변경후": 0}]
    assert r.verification.누계_후 == r.verification.누계_전


def test_issue_example_attachment(ledger, book):
    from core.paths import TEMPLATES_DIR
    path = TEMPLATES_DIR / "examples" / "report_childrensday.hwpx"
    if not path.exists():
        pytest.skip("작성 예시 없음(커밋 제외 파일)")
    att = read_document(path)
    out = _issue_out("정정", [{"순번": 25, "열": "본당보조", "변경후": "490000", "사유": "본당 = min(신청, 실사용)"}])
    fake = FakeClient(tool_response("submit_issue_assessment", out))
    r = issue_agent.assist_issue("I-012", ledger, book, "첨부한 보고서 기준으로 정리해줘", attachment=att, client=fake)
    assert {"순번": 25, "열": "(첨부 세부내역 합)", "장부": 490_060, "첨부": 490_070} in r.첨부대조
    assert r.해결유형 == "정정" and r.diff[0]["변경후"] == 490_000
    attached = fake.prompt().split("[첨부 문서")[1].split("[첨부 읽기")[0]
    assert "계좌번호" not in attached and "연락처" not in attached  # 민감 칸은 보내지 않는다


def test_issue_unclear_returns_questions(ledger, book):
    out = _issue_out("추가 확인 필요", questions=["부서 운영비가 실제로 지급되었나요?", "지급됐다면 영수증이 있나요?"])
    r = issue_agent.assist_issue("I-019", ledger, book, "잘 모르겠어",
                                 client=FakeClient(tool_response("submit_issue_assessment", out)))
    assert r.해결유형 == "추가 확인 필요" and r.resolution is None and r.verification is None
    assert len(r.추가질문) == 2 and r.설명문


def test_issue_guards(ledger, book):
    # 대상 밖 행 변경만 있는 정정 → 받아들이지 않고 추가 확인 필요
    out = _issue_out("정정", [{"순번": 1, "열": "금번신청액", "변경후": "0", "사유": "x"}])
    r = issue_agent.assist_issue("I-019", ledger, book, "x",
                                 client=FakeClient(tool_response("submit_issue_assessment", out)))
    assert r.해결유형 == "추가 확인 필요" and any("이슈 대상이 아니" in p for p in r.problems)
    # 검증 실패(합계가 맞지 않는 보고서 행 추가) → 추가 확인 필요
    bad = {c: None for c in issue_agent.ROW_FIELDS} | {
        "월": "7월", "구분": "집행보고서", "작성일": "2026년 10월 23일", "자체예산": 0, "본당보조": 480000,
        "합계": 400000, "잔액": 0, "문서번호": "", "행사명": "부서 운영비", "파일명": "", "사업ID": "P13",
        "연결문서번호": "(예)2026-037", "상태": "", "양식": "신"}
    for k in ("예산배정액", "금번신청액", "신청누계액", "잔여액"):
        bad[k] = None
    r = issue_agent.assist_issue("I-019", ledger, book, "지급됐어",
                                 client=FakeClient(tool_response("submit_issue_assessment", _issue_out("정정", rows=[bad]))))
    assert r.해결유형 == "추가 확인 필요" and any("검증 실패" in p for p in r.problems)


# ---------------------------------------------------------------- 이슈 설명문(자리표시자)

def test_explanation_placeholders_filled(ledger, book):
    fake = FakeClient(tool_response("submit_issue_assessment", _issue_out("추가 확인 필요", questions=["?"])))
    r = issue_agent.assist_issue("I-019", ledger, book, "잘 모르겠어", client=fake)
    assert r.설명문.startswith("(예)2026-037 부서 운영비에 대한 집행보고서가 없고 480,000원이")
    assert "12,775,990원" in r.설명문 and "{" not in r.설명문 and len(fake.calls) == 1
    assert "{잔여액}: 재계산 기준 잔여 예산" in fake.prompt()


def test_explanation_with_numbers_is_retried(ledger, book):
    bad = _issue_out("추가 확인 필요", questions=["?"])
    bad["설명문"] = "부서 운영비 480,000원이 빠졌습니다. 잔여액은 13,253140원입니다."
    fake = FakeClient(tool_response("submit_issue_assessment", bad),
                      tool_response("submit_explanation", {"설명문": "{대상금액}이 누계에 빠졌습니다. 확인해 주세요."}))
    r = issue_agent.assist_issue("I-019", ledger, book, "잘 모르겠어", client=fake)
    assert len(fake.calls) == 2 and fake.calls[1]["tools"][0]["name"] == "submit_explanation"
    assert "자리표시자가 아닌 숫자: 480,000, 13,253140" in fake.prompt(1)
    assert r.설명문 == "480,000원이 누계에 빠졌습니다. 확인해 주세요." and len(r.calls) == 2


def test_explanation_still_bad_after_retry(ledger, book):
    bad = _issue_out("추가 확인 필요", questions=["?"])
    bad["설명문"] = "{없는값}입니다."
    still = "{대상문서}가 문제입니다. 7월에 냈습니다. 둘째 문장입니다. 셋째 문장입니다. 넷째 문장입니다. 다섯째 문장입니다."
    fake = FakeClient(tool_response("submit_issue_assessment", bad),
                      tool_response("submit_explanation", {"설명문": still}))
    r = issue_agent.assist_issue("I-019", ledger, book, "x", client=fake)
    assert "없는 자리표시자: 없는값" in fake.prompt(1)
    assert r.설명문 == "(예)2026-037 부서 운영비가 문제입니다. 둘째 문장입니다. 셋째 문장입니다. 넷째 문장입니다."
    assert any("7월에 냈습니다" in p for p in r.problems) and any("앞 4문장" in p for p in r.problems)


# ---------------------------------------------------------------- 영수증: 대행사·중복

def test_receipt_pg_and_duplicates(ledger, tmp_path):
    Image.new("RGB", (100, 100), "white").save(tmp_path / "a.png")
    base = {"날짜": "2026-08-17", "금액": 141790, "품목요약": "게임 선물", "결제수단": "신용카드",
            "확인필요": False, "확인사유": ""}
    fake = FakeClient(
        tool_response("submit_receipt", base | {"상호": "나이스정보통신(주)", "가맹점명_원문": "나이스정보통신(주)",
                                               "승인번호": "66496374"}),
        tool_response("submit_receipt", base | {"상호": "엔에이치엔케이씨피 주식회사", "가맹점명_원문": "엔에이치엔케이씨피 주식회사",
                                               "승인번호": "6649 6374"}),
        tool_response("submit_receipt", base | {"상호": "바른치킨 개포점", "가맹점명_원문": "바른치킨 개포점",
                                               "승인번호": "", "금액": 48400}),
    )
    out = receipt_agent.extract_receipts([tmp_path / "a.png"] * 3, ledger, client=fake)
    assert [r.대행사표기 for r in out] == [True, True, False]
    assert out[0].확인필요 is False and "대행사 표기(나이스정보통신(주))" in out[0].확인사유
    assert (out[0].승인번호, out[0].결제수단) == ("66496374", "신용카드")
    dup = receipt_agent.duplicate_warnings(out)
    assert dup == ["중복 의심: 영수증 1번(a.png), 2번(a.png) — 승인번호 66496374, 141,790원, 2026-08-17"]


def test_report_prefers_store_from_note(ledger):
    receipts = [ReceiptExtraction(1, "a.pdf", date(2026, 8, 17), "나이스정보통신(주)", 141_790, "게임 선물", False,
                                  가맹점명_원문="나이스정보통신(주)", 대행사표기=True),
                ReceiptExtraction(2, "b.pdf", date(2026, 8, 17), "엔에이치엔케이씨피 주식회사", 53_580, "꾸미기", False,
                                  가맹점명_원문="엔에이치엔케이씨피 주식회사", 대행사표기=True)]
    out = _report_output(경과보고_줄=[], 세부내역=[{"계정과목": "행사용품비", "내용": "게임 선물", "영수증번호": [1, 2]}],
                         대행사_상호=[{"영수증번호": 1, "상호": "쿠팡"}])
    fake = FakeClient(tool_response("submit_report_draft", out))
    d = report_agent.draft_report(ledger.row(44), receipts, ledger, progress_note="8/17 쿠팡에서 게임 선물",
                                  client=fake, today=TODAY)
    assert "[대행사 표기]" in fake.prompt()
    assert receipts[0].상호_확정 == "쿠팡" and receipts[0].to_receipt().상호 == "쿠팡"
    assert receipts[1].상호_확정 == "엔에이치엔케이씨피 주식회사"
    assert any("2번 가맹점명이 결제대행사" in w for w in d.warnings)
    assert not any("1번 가맹점명이 결제대행사" in w for w in d.warnings)


def test_fill_josa():
    v = {"금액": "23,216,860원", "사업": "P13 부서 운영비", "달": "7월", "문서": "(예)2026-037"}
    f = issue_agent.fill
    assert f("{금액}와 {금액}가 다르고 {사업}은 {금액}로 {달}으로", v) == \
        "23,216,860원과 23,216,860원이 다르고 P13 부서 운영비는 23,216,860원으로 7월로"
    assert f("{사업}을 보고 {문서}를 확인", v) == "P13 부서 운영비를 보고 (예)2026-037를 확인"  # 한글이 아니면 그대로
    assert f("{없음}은 그대로", v) == "{없음}은 그대로"


def test_questions_placeholders_filled(ledger, book):
    out = _issue_out("추가 확인 필요", questions=["{대상문서}가 지급됐나요?"])
    r = issue_agent.assist_issue("I-019", ledger, book, "x",
                                 client=FakeClient(tool_response("submit_issue_assessment", out)))
    assert r.추가질문 == ["(예)2026-037 부서 운영비가 지급됐나요?"]


def test_participant_given_name():
    roster = ["정연재 레오", "정지연 플로라", "양준형 사도요한"]
    assert report_agent.match_participant("연재", roster) == "정연재 레오"
    assert report_agent.participant_candidates("지연", roster) == ["정지연 플로라"]
    assert report_agent.match_participant("재", roster) is None


def test_plan_photo_extraction(ledger, tmp_path):
    Image.new("RGB", (200, 100), "white").save(tmp_path / "plan.jpg")
    out = {"단위": "천원", "사업목록": [
        {"사업ID": "P01", "시기": "1월", "계속신규": "계속", "사업명": "졸업행사", "내역": "꽃다발", "자체": 0,
         "본당": 1300, "전년도": 1177, "월별": [1300] + [0] * 11},
        {"사업ID": "", "시기": "연중", "계속신규": "계속", "사업명": "운영비", "내역": "", "자체": 0, "본당": 600,
         "전년도": None, "월별": [50] * 11}],
        "개요": [{"항목": "사업목표", "내용": "신앙 함양"}], "읽기메모": ["3행 흐림"]}
    fake = FakeClient(tool_response("submit_plan", out))
    ext = plan_agent.extract_plan([tmp_path / "plan.jpg"], ledger, client=fake)
    assert ext.plan[0]["본당예산"] == 1_300_000 and ext.plan[0]["1월"] == 1_300_000 and ext.plan[0]["전년도집행액"] == 1_177_000
    assert ext.plan[1]["사업ID"] == "P02" and ext.plan[1]["12월"] == 0
    assert "3행 흐림" in ext.notes and any("월별 칸이 11개" in n for n in ext.notes)
    from core.plan_io import plan_problems
    assert any("P02" in p for p in plan_problems(ext.plan))  # 50×11 ≠ 600
