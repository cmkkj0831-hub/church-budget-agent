"""Claude API 공통 호출부. 에이전트는 모두 '도구 1개를 호출해 JSON을 제출'하는 방식으로 출력을 강제한다.

- 모델: settings.json `모델`(기본 claude-sonnet-5-5)
- API 키: 환경변수 ANTHROPIC_API_KEY → st.secrets → .streamlit/secrets.toml
- Claude Sonnet 5.5는 강제 tool_choice(any/tool)를 받지 않으므로 auto + strict 스키마 + 지시문으로 호출을 유도하고,
  도구 호출이 없으면 한 번 다시 요청한다.
- 거절(refusal)에 대비해 서버 측 대체 모델(fallbacks="default")을 켠다.
"""
import base64
import io
import os
from dataclasses import dataclass, field

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

def nullable(schema: dict) -> dict:
    t = schema["type"]
    out = {**schema, "type": [t, "null"] if isinstance(t, str) else [*t, "null"]}
    if "enum" in schema:
        out["enum"] = [*schema["enum"], None]
    return out


def obj(properties: dict, description: str = "") -> dict:
    """strict 도구용 object: 모든 속성 required, 추가 속성 금지."""
    out = {"type": "object", "properties": properties, "required": list(properties),
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
class ToolCall:
    input: dict
    model: str
    usage: dict = field(default_factory=dict)


def call_tool(client, model: str, system: str, content: list | str, spec: dict,
              effort: str = "medium", max_tokens: int = MAX_TOKENS) -> ToolCall:
    """spec 도구를 한 번 호출하게 하고 그 입력(JSON)을 돌려준다."""
    instruction = (f"\n\n결과는 반드시 `{spec['name']}` 도구를 정확히 한 번 호출해 제출하라. "
                   "도구 밖의 텍스트 답변은 쓰지 않는다. 모르는 값은 추측하지 말고 null로 둔다.")
    messages = [{"role": "user", "content": content}]
    for attempt in range(2):
        response = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system + instruction,
            messages=messages,
            tools=[spec],
            tool_choice={"type": "auto"},
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise AgentRefusal(f"요청이 거절됨: {getattr(response, 'stop_details', None)}")
        if response.stop_reason == "max_tokens":
            raise AgentError("응답이 max_tokens에서 잘림")
        for block in response.content:
            if block.type == "tool_use" and block.name == spec["name"]:
                usage = getattr(response, "usage", None)
                return ToolCall(dict(block.input), getattr(response, "model", model),
                                usage.model_dump() if hasattr(usage, "model_dump") else {})
        # 도구를 부르지 않았으면 한 번 더 요청
        messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": f"`{spec['name']}` 도구를 호출해 결과를 제출해 주세요."},
        ]
    raise AgentError(f"`{spec['name']}` 도구 호출을 받지 못함")


def model_of(ledger) -> str:
    return getattr(ledger.settings, "모델", None) or DEFAULT_MODEL
