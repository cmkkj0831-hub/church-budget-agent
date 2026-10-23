"""Claude API 대역(mock). 응답을 차례로 돌려주고 받은 요청을 기록한다."""
from types import SimpleNamespace


def tool_response(name: str, payload: dict, stop_reason: str = "tool_use"):
    block = SimpleNamespace(type="tool_use", name=name, input=payload, id="toolu_test")
    return SimpleNamespace(stop_reason=stop_reason, content=[block], model="claude-sonnet-5-5", usage=None)


def text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)],
                           model="claude-sonnet-5-5", usage=None, stop_details=None)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        return r(kwargs) if callable(r) else r

    def prompt(self, n: int = -1) -> str:
        """n번째 요청의 system + 첫 user 텍스트."""
        call = self.calls[n]
        content = call["messages"][0]["content"]
        text = content if isinstance(content, str) else " ".join(
            c.get("text", "") for c in content if c.get("type") == "text")
        return call["system"] + "\n" + text
