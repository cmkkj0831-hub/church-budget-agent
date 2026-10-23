"""Claude API 공통 호출부. 에이전트는 모두 '도구 1개를 호출해 JSON을 제출'하는 방식으로 출력을 강제한다.

- 모델: settings.json `모델`(기본 claude-sonnet-5-5)
- API 키: 환경변수 ANTHROPIC_API_KEY → st.secrets → .streamlit/secrets.toml (키 값은 로그·출력에 남기지 않는다)
- 도구 호출 강제: Claude Sonnet 5.5(그리고 Opus 5.5, Fable 5.1, Mythos 5.1)는 tool_choice "any"/"tool"에 400을 돌려준다.
  대신 tool_choice "auto" + strict 도구 + 지시문으로 유도하고, 도구 호출이 없으면 한 번 다시 요청한다.
  근거: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#forcing-tool-use
        (표 "Claude Opus 5.5, Claude Sonnet 5.5, Claude Fable 5.1, and Claude Mythos 5.1 | any and tool return a 400 error
         | auto with strict tool use ...")
        strict 도구: https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use
- 거절 대비 서버 측 대체(fallbacks): settings `llm_fallbacks`가 "default"일 때만 켠다(기본 "off").
  켜면 beta 헤더 server-side-fallback-2026-07-01 + fallbacks="default" — 거절 범주별 권장 모델로 같은 요청을 다시 돌린다.
  응답의 top-level `model`이 실제로 답한 모델이고, usage.iterations에 type "fallback_message"가 있으면 대체 모델이 돈 것이다.
  근거: https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#server-side-fallback
        ("Set the fallbacks parameter to the string "default" and send the server-side-fallback-2026-07-01 beta header",
         "The top-level model field reports the model that produced the returned message, whether that is the
          requested model or a fallback", 과금은 시도별로 usage.iterations에 기록)
- 호출마다 요청 모델·실제 응답 모델·대체 여부·소요 시간·토큰을 CallInfo로 남긴다.
"""
import base64
import io
import os
import re
import time
from dataclasses import dataclass

import anthropic

from core.secrets import load_secrets

DEFAULT_MODEL = "claude-sonnet-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000


class AgentError(RuntimeError):
    pass


class AgentRefusal(AgentError):
    pass


def api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:  # Streamlit 앱 안에서 실행될 때
        import streamlit as st
        key = st.secrets.get("ANTHROPIC_API_KEY")
        if key:
            return key
    except Exception:
        pass
    return load_secrets().get("ANTHROPIC_API_KEY") or None


def make_client() -> anthropic.Anthropic:
    key = api_key()
    if not key:
        raise AgentError("ANTHROPIC_API_KEY가 없습니다(환경변수 또는 .streamlit/secrets.toml)")
    return anthropic.Anthropic(api_key=key)


# ---------------------------------------------------------------- 스키마 도우미
# 도구 스키마의 속성 이름은 ^[a-zA-Z0-9_.-]{1,64}$만 허용된다(한글 키는 400 — 실제 호출로 확인).
# 코드에서는 한글 키를 쓰고, 보낼 때 영문 별칭으로 바꾸고 받을 때 되돌린다. 설명에는 한글 이름을 붙인다.
KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
MAX_UNION_PARAMS = 16  # strict 스키마의 null 허용·anyOf 속성 수 한도(실제 호출 400 메시지로 확인)
ALIASES = {
    # 공통·신청서
    "행사명": "event_name", "대상월": "target_month", "행사유형": "event_type", "목적": "purpose",
    "시작일": "start_date", "시작시각": "start_time", "종료일": "end_date", "종료시각": "end_time",
    "박": "nights", "일": "days", "장소": "place", "참가대상": "audience", "인원_총": "people_total",
    "인원_남": "people_male", "인원_여": "people_female", "세부항목": "items", "계정과목": "account",
    "내용": "description", "단가": "unit_price", "수량": "quantity", "사업ID_후보": "business_candidates",
    "사업ID": "business_id", "근거": "reason", "행사일정": "schedule_text",
    # 영수증
    "날짜": "date", "상호": "merchant", "금액": "amount", "품목요약": "item_summary",
    "확인필요": "needs_review", "확인사유": "review_reason", "승인번호": "approval_number",
    "결제수단": "payment_method", "가맹점명_원문": "merchant_raw", "대행사_상호": "pg_store_names",
    # 보고서
    "머리말": "intro", "경과보고_줄": "progress_lines", "항목": "item", "영수증번호": "receipt_numbers",
    "참가자": "participants", "노트_언급": "from_note", "노트_날짜": "note_date", "노트_금액": "note_amount",
    "세부내역": "line_items", "건의사항": "suggestion", "불일치_메모": "mismatch_notes",
    "행사명_제안": "suggested_event_name",
    # 이슈
    "해결유형": "resolution_type", "변경안": "changes", "순번": "seq", "열": "column", "변경후": "new_value",
    "사유": "why", "추가행": "rows_to_add", "함께해결": "resolve_together", "해결내용": "resolution_note",
    "남은의문": "open_questions", "추가질문": "followup_questions", "설명문": "explanation",
    # 장부 열(추가행)
    "월": "month", "구분": "kind", "작성일": "written_date", "예산배정액": "allocation",
    "금번신청액": "requested_amount", "신청누계액": "cumulative_requested", "잔여액": "remaining",
    "자체예산": "own_funds", "본당보조": "parish_subsidy", "합계": "total", "잔액": "balance",
    "문서번호": "doc_number", "파일명": "file_name", "연결문서번호": "linked_doc_number", "상태": "status",
    "양식": "form",
}
REVERSE = {v: k for k, v in ALIASES.items()}
assert len(REVERSE) == len(ALIASES), "영문 별칭 중복"


def alias(key: str) -> str:
    out = ALIASES.get(key, key)
    if not KEY_PATTERN.match(out):
        raise ValueError(f"도구 속성 '{key}'에 영문 별칭이 없음(ALIASES에 추가)")
    return out


def unalias(value):
    """도구 입력(JSON)의 영문 키를 한글 키로 되돌린다."""
    if isinstance(value, dict):
        return {REVERSE.get(k, k): unalias(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unalias(v) for v in value]
    return value

def nullable(schema: dict) -> dict:
    """null 허용. enum은 type 배열로 쓰면 400이라(실제 호출로 확인) anyOf로 감싼다."""
    if "enum" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    t = schema["type"]
    return {**schema, "type": [t, "null"] if isinstance(t, str) else [*t, "null"]}


def obj(properties: dict, description: str = "") -> dict:
    """strict 도구용 object: 모든 속성 required, 추가 속성 금지. 한글 속성 이름은 영문 별칭으로 바꾼다."""
    props = {}
    for key, schema in properties.items():
        name = alias(key)
        if name != key:
            schema = {**schema, "description": key + (f": {schema['description']}" if schema.get("description") else "")}
        props[name] = schema
    out = {"type": "object", "properties": props, "required": list(props),
           "additionalProperties": False}
    if description:
        out["description"] = description
    return out


def arr(items: dict, description: str = "") -> dict:
    out = {"type": "array", "items": items}
    if description:
        out["description"] = description
    return out


def s(description: str = "") -> dict:
    return {"type": "string", "description": description} if description else {"type": "string"}


def i(description: str = "") -> dict:
    return {"type": "integer", "description": description} if description else {"type": "integer"}


def tool(name: str, description: str, schema: dict) -> dict:
    return {"name": name, "description": description, "input_schema": schema, "strict": True}


def image_block(jpeg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpeg).decode()}}


def jpeg_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ---------------------------------------------------------------- 호출

@dataclass
class CallInfo:
    """API 호출 1회(재요청 포함)의 기록."""
    tool: str
    requested_model: str
    model: str                      # 실제로 응답한 모델(response.model)
    served_by_fallback: bool
    seconds: float
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def line(self) -> str:
        fb = " (대체 모델)" if self.served_by_fallback else ""
        return (f"{self.tool}: {self.model}{fb} {self.seconds:.1f}초, 입력 {self.input_tokens:,} / "
                f"출력 {self.output_tokens:,} 토큰" + (f", 재요청 {self.attempts - 1}회" if self.attempts > 1 else ""))


@dataclass
class ToolCall:
    input: dict
    info: CallInfo

    @property
    def model(self) -> str:
        return self.info.model


def _usage_counts(usage) -> dict:
    get = (lambda k: getattr(usage, k, None) or 0) if usage is not None else (lambda k: 0)
    return {"input_tokens": get("input_tokens"), "output_tokens": get("output_tokens"),
            "cache_read_tokens": get("cache_read_input_tokens"),
            "cache_creation_tokens": get("cache_creation_input_tokens")}


def _fallback_ran(response) -> bool:
    iterations = getattr(getattr(response, "usage", None), "iterations", None) or []
    return any(getattr(it, "type", None) == "fallback_message" for it in iterations) or \
        any(getattr(b, "type", None) == "fallback" for b in response.content)


def call_tool(client, model: str, system: str, content: list | str, spec: dict,
              effort: str = "medium", max_tokens: int = MAX_TOKENS, fallbacks: str = "off") -> ToolCall:
    """spec 도구를 한 번 호출하게 하고 그 입력(JSON)과 호출 기록을 돌려준다."""
    instruction = (f"\n\n결과는 반드시 `{spec['name']}` 도구를 정확히 한 번 호출해 제출하라. "
                   "도구 밖의 텍스트 답변은 쓰지 않는다. 모르는 값은 추측하지 말고 null(글자 칸은 빈 문자열)로 둔다.")
    messages = [{"role": "user", "content": content}]
    params = dict(model=model, max_tokens=max_tokens, system=system + instruction, tools=[spec],
                  tool_choice={"type": "auto"}, output_config={"effort": effort})
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    started = time.monotonic()
    for attempt in range(1, 3):
        if fallbacks == "default":
            response = client.beta.messages.create(**params, messages=messages, betas=[FALLBACK_BETA],
                                                   fallbacks="default")
        else:
            response = client.messages.create(**params, messages=messages)
        for k, v in _usage_counts(getattr(response, "usage", None)).items():
            totals[k] += v
        if response.stop_reason == "refusal":
            raise AgentRefusal(f"요청이 거절됨({getattr(response, 'model', model)}): "
                               f"{getattr(response, 'stop_details', None)}")
        if response.stop_reason == "max_tokens":
            raise AgentError("응답이 max_tokens에서 잘림")
        for block in response.content:
            if block.type == "tool_use" and block.name == spec["name"]:
                info = CallInfo(spec["name"], model, getattr(response, "model", None) or model,
                                _fallback_ran(response), time.monotonic() - started, attempt, **totals)
                return ToolCall(unalias(dict(block.input)), info)
        # 도구를 부르지 않았으면 한 번 더 요청
        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": f"`{spec['name']}` 도구를 호출해 결과를 제출해 주세요."},
        ]
    raise AgentError(f"`{spec['name']}` 도구 호출을 받지 못함")


def model_of(ledger) -> str:
    return getattr(ledger.settings, "모델", None) or DEFAULT_MODEL


def fallbacks_of(ledger) -> str:
    return getattr(ledger.settings, "llm_fallbacks", "off") or "off"


def call(ledger, client, system, content, spec, effort: str = "medium") -> ToolCall:
    """settings의 모델·fallbacks로 call_tool."""
    return call_tool(client or make_client(), model_of(ledger), system, content, spec, effort=effort,
                     fallbacks=fallbacks_of(ledger))
