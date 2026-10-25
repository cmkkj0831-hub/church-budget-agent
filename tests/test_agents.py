"""에이전트 단위 테스트. Claude API는 tests/fakes.FakeClient로 대신한다(실제 호출은 test_agents_live.py, --live)."""
import base64
import io
from datetime import date, datetime

import pytest
from PIL import Image

from agents import receipt_agent, report_agent
from agents.llm import FALLBACK_BETA, KEY_PATTERN, MAX_UNION_PARAMS, AgentError, AgentRefusal, call_tool, unalias
from agents.receipt_agent import ReceiptExtraction
from core.models import ReportDoc
from hwpx.fill import render
from hwpx.reader import read_document
from core.report import build_report
from types import SimpleNamespace

from tests.fakes import FakeClient, text_response, tool_response, usage

ALL_SPECS = [receipt_agent.SPEC, report_agent.SPEC]


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


def test_call_tool_request_shape_and_retry():
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


def test_fallbacks_option_and_served_model(settings):
    settings.llm_fallbacks = "default"
    it = [SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]
    fake = FakeClient(tool_response("submit_report_draft", _report_output(), model="claude-opus-4-8",
                                    usage=usage(100, 10, it), fallback_from="claude-sonnet-5-5"))
    d = report_agent.compose_report(settings, "", RECEIPTS[:3], client=fake)
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


def test_model_comes_from_settings(settings):
    fake = FakeClient(tool_response("submit_report_draft", _report_output()))
    report_agent.compose_report(settings, "", RECEIPTS[:3], client=fake)
    assert fake.calls[0]["model"] == settings.모델 == "claude-sonnet-5-5"


# ---------------------------------------------------------------- 2. 영수증

def test_receipts_rotation_and_unreadable_amount(settings, tmp_path):
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
    out = receipt_agent.extract_receipts([tmp_path / "rot.jpg", tmp_path / "b.png"], settings, client=fake)
    sent = fake.calls[0]["messages"][0]["content"][0]["source"]
    shown = Image.open(io.BytesIO(base64.b64decode(sent["data"])))
    assert sent["media_type"] == "image/jpeg" and shown.height > shown.width  # EXIF 회전 반영
    assert (out[0].금액, out[0].확인필요, out[0].날짜) == (281000, False, date(2026, 8, 15))
    assert out[1].금액 is None and out[1].확인필요 and "금액을 읽지 못함" in out[1].확인사유
    assert out[0].to_receipt().용도 == "식사"


def test_receipts_heic(settings, tmp_path):
    pytest.importorskip("pillow_heif")
    try:
        Image.new("RGB", (300, 200), "white").save(tmp_path / "a.heic")
    except Exception:
        pytest.skip("HEIC 저장 불가 환경")
    fake = FakeClient(tool_response("submit_receipt", {"날짜": "2026-08-15", "상호": "가게", "금액": 1000,
                                                       "품목요약": "간식", "확인필요": False, "확인사유": ""}))
    assert receipt_agent.extract_receipts([tmp_path / "a.heic"], settings, client=fake)[0].금액 == 1000


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
        "불일치_메모": [],
        "대행사_상호": [],
    }
    out.update(over)
    return out


def test_report_draft_with_note(settings):
    fake = FakeClient(tool_response("submit_report_draft", _report_output()))
    note = "8/15 미사 후 식대 29만원 연재, 플로라, 김철수 / 8/14 회합 후 식대 43,300원 연재"
    comp = report_agent.compose_report(settings, "행사명: 8월 교사회 운영비\n신청액: 540,000원", RECEIPTS,
                                       progress_note=note, client=fake)
    lines = comp.경과보고.split("\n")
    assert lines[0] == "8월 교사회 운영비를 다음과 같이 사용하였습니다."
    assert lines[1] == "8월 15일 미사 후 식대 : 292,100원 / 참가자 : 정연재 레오, 정지연 플로라, 김철수"
    assert lines[2] == "8월 16일 회합 후 식대 : 43,300원 / 참가자 : 정연재 레오"
    # 세부내역 금액은 영수증 합(LLM이 아님). 3번이 두 곳에 들어가 한 번만, 4번은 따로
    assert [(i.계정과목, i.단가) for i in comp.세부내역] == [("식대", 324_300), ("다과비", 11_100), ("기타", 0)]
    w = "\n".join(comp.warnings)
    for expected in ("명단에 없는 참가자: '김철수'", "금액 불일치: '8월 15일 미사 후 식대' 노트 290,000원 / 영수증 292,100원",
                     "날짜 불일치: '회합 후 식대' 노트 8월 14일 / 영수증 8월 16일", "progress_note에 없는 영수증: 4번 다이소",
                     "영수증 3번이 세부내역 두 곳", "영수증 4번(다이소) 확인 필요"):
        assert expected in w, expected
    prompt = fake.prompt()
    assert report_agent.FORMAT_EXAMPLE.split("\n")[1] in prompt
    assert "[행사 정보 — 회계담당 입력]\n행사명: 8월 교사회 운영비" in prompt and "suggestion" not in prompt
    # 보고서 금액은 core 규칙: 신청 540,000, 실사용 335,400 → 반납 204,600
    doc = build_report(ReportDoc(월=8, 행사명="8월 교사회 운영비", 세부내역=comp.세부내역), settings, 540_000).doc
    assert (doc.본당보조, doc.자체예산, doc.합계B, doc.예산A, doc.잔액) == (335_400, 0, 335_400, 540_000, 204_600)


def test_report_draft_without_note(settings):
    out = _report_output(경과보고_줄=[{"날짜": None, "항목": "식대", "영수증번호": [1], "참가자": [],
                                    "노트_언급": False, "노트_날짜": None, "노트_금액": None}])
    fake = FakeClient(tool_response("submit_report_draft", out))
    comp = report_agent.compose_report(settings, "", RECEIPTS[:3], client=fake)
    assert comp.lines[0].text() == "8월 15일 식대 : 281,000원"
    assert not any("progress_note" in x for x in comp.warnings)
    assert "[행사 정보 — 회계담당 입력]\n(없음)" in fake.prompt()


@pytest.mark.parametrize("applied, used, expected", [
    (500_000, 300_000, (300_000, 0, 300_000, 500_000, 200_000)),   # 덜 씀: 반납
    (300_000, 350_000, (300_000, 50_000, 350_000, 350_000, 0)),    # 더 씀: 초과분 자체
    (0, 10_000, (0, 10_000, 10_000, 10_000, 0)),                   # 신청 없음
])
def test_report_amount_rules(settings, applied, used, expected):
    from core.models import LineItem
    doc = build_report(ReportDoc(월=10, 행사명="x", 세부내역=[LineItem(내용="a", 단가=used)]), settings, applied).doc
    assert (doc.본당보조, doc.자체예산, doc.합계B, doc.예산A, doc.잔액) == expected
    assert doc.예산A - doc.합계B == doc.잔액 and doc.본당보조 + doc.자체예산 == doc.합계B


def test_participant_matching():
    roster = ["정연재 레오", "양준형 사도요한", "조민기 사도요한", "정지연 플로라"]
    m = report_agent.match_participant
    assert m("정연재", roster) == "정연재 레오" and m("플로라", roster) == "정지연 플로라"
    assert m("사도요한", roster) is None  # 세례명이 둘이면 판단하지 않는다
    assert report_agent.participant_candidates("사도요한", roster) == ["양준형 사도요한", "조민기 사도요한"]
    assert m("정연재 바오로", roster) is None and report_agent.participant_candidates("정연재 바오로", roster) == []


def test_ambiguous_participant_returns_candidates(settings):
    out = _report_output(경과보고_줄=[{"날짜": "8월 15일", "항목": "미사 후 식대", "영수증번호": [1, 2, 3],
                                    "참가자": ["사도요한", "정연재"], "노트_언급": False, "노트_날짜": None,
                                    "노트_금액": None}])
    d = report_agent.compose_report(settings, "", RECEIPTS[:3],
                                    client=FakeClient(tool_response("submit_report_draft", out)))
    assert d.참가자_선택 == [{"줄": "8월 15일 미사 후 식대", "입력": "사도요한",
                                       "후보": ["양준형 사도요한", "조민기 사도요한"]}]
    assert any("참가자 확인 필요: '사도요한' → 후보 양준형 사도요한, 조민기 사도요한" in w for w in d.warnings)
    assert "참가자 : 사도요한, 정연재 레오" in d.경과보고  # 고르기 전에는 입력 그대로


# ---------------------------------------------------------------- 영수증: 대행사·중복

def test_receipt_pg_and_duplicates(settings, tmp_path):
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
    out = receipt_agent.extract_receipts([tmp_path / "a.png"] * 3, settings, client=fake)
    assert [r.대행사표기 for r in out] == [True, True, False]
    assert out[0].확인필요 is False and "대행사 표기(나이스정보통신(주))" in out[0].확인사유
    assert (out[0].승인번호, out[0].결제수단) == ("66496374", "신용카드")
    dup = receipt_agent.duplicate_warnings(out)
    assert dup == ["중복 의심: 영수증 1번(a.png), 2번(a.png) — 승인번호 66496374, 141,790원, 2026-08-17"]


def test_report_prefers_store_from_note(settings):
    receipts = [ReceiptExtraction(1, "a.pdf", date(2026, 8, 17), "나이스정보통신(주)", 141_790, "게임 선물", False,
                                  가맹점명_원문="나이스정보통신(주)", 대행사표기=True),
                ReceiptExtraction(2, "b.pdf", date(2026, 8, 17), "엔에이치엔케이씨피 주식회사", 53_580, "꾸미기", False,
                                  가맹점명_원문="엔에이치엔케이씨피 주식회사", 대행사표기=True)]
    out = _report_output(경과보고_줄=[], 세부내역=[{"계정과목": "행사용품비", "내용": "게임 선물", "영수증번호": [1, 2]}],
                         대행사_상호=[{"영수증번호": 1, "상호": "쿠팡"}])
    fake = FakeClient(tool_response("submit_report_draft", out))
    d = report_agent.compose_report(settings, "", receipts, progress_note="8/17 쿠팡에서 게임 선물", client=fake)
    assert "[대행사 표기]" in fake.prompt()
    assert receipts[0].상호_확정 == "쿠팡" and receipts[0].to_receipt().상호 == "쿠팡"
    assert receipts[1].상호_확정 == "엔에이치엔케이씨피 주식회사"
    assert any("2번 가맹점명이 결제대행사" in w for w in d.warnings)
    assert not any("1번 가맹점명이 결제대행사" in w for w in d.warnings)


def test_participant_given_name():
    roster = ["정연재 레오", "정지연 플로라", "양준형 사도요한"]
    assert report_agent.match_participant("연재", roster) == "정연재 레오"
    assert report_agent.participant_candidates("지연", roster) == ["정지연 플로라"]
    assert report_agent.match_participant("재", roster) is None
