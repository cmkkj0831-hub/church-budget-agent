"""사업계획서 사진(여러 장) → 사업 행. 금액은 문서에 적힌 숫자 그대로 받고(단위 함께), 원 단위 환산·검증은 코드가 한다."""
from dataclasses import dataclass, field
from pathlib import Path

from agents.llm import CallInfo, arr, call, i, image_block, jpeg_bytes, nullable, obj, s, tool
from core.plan_io import MONTHS

SYSTEM = """너는 천주교 성당 초등부 교사회의 연간 사업계획서 표를 옮겨 적는다.
- 표의 각 사업 행을 위에서부터 순서대로 옮긴다. 사업ID가 없으면 P01, P02… 순서로 붙인다.
- 금액은 표에 적힌 숫자 그대로 적고, 표의 단위(천원 또는 원)를 단위에 적는다. 환산하지 않는다.
- 월별은 1월~12월 12칸. 빈 칸은 0.
- 합계 행은 옮기지 않는다. 읽기 어려운 칸이 있으면 0으로 두지 말고 읽기메모에 적는다(추측 금지).
- 개요는 표 아래 사업목표·사업내용·비고 같은 문장(항목, 내용)."""

SPEC = tool("submit_plan", "사업계획서 표 제출", obj({
    "단위": {"type": "string", "enum": ["천원", "원"]},
    "사업목록": arr(obj({
        "사업ID": s(), "시기": s(), "계속신규": s(), "사업명": s(), "내역": s(),
        "자체": i(), "본당": i(), "전년도": nullable(i()), "월별": arr(i(), "1월~12월 12칸"),
    })),
    "개요": arr(obj({"항목": s(), "내용": s()})),
    "읽기메모": arr(s()),
}))


@dataclass
class PlanExtraction:
    plan: list[dict]
    info: list[dict]
    notes: list[str] = field(default_factory=list)
    calls: list[CallInfo] = field(default_factory=list)


def to_plan(out: dict) -> PlanExtraction:
    unit = 1000 if out.get("단위") == "천원" else 1
    notes = list(out.get("읽기메모") or [])
    plan = []
    for n, p in enumerate(out.get("사업목록") or [], 1):
        months = list(p.get("월별") or [])
        if len(months) != 12:
            notes.append(f"{p.get('사업ID') or n}: 월별 칸이 {len(months)}개(12개여야 함) — 확인 필요")
            months = (months + [0] * 12)[:12]
        row = {"사업ID": p.get("사업ID") or f"P{n:02d}", "시기": p.get("시기", ""), "계속신규": p.get("계속신규", ""),
               "사업명": p.get("사업명", ""), "세부내역": p.get("내역", ""), "자체예산": int(p.get("자체") or 0) * unit,
               "본당예산": int(p.get("본당") or 0) * unit,
               "전년도집행액": "" if p.get("전년도") is None else int(p["전년도"]) * unit}
        row |= {m: int(v or 0) * unit for m, v in zip(MONTHS, months)}
        plan.append(row)
    return PlanExtraction(plan, [dict(x) for x in out.get("개요") or []], notes)


def extract_plan(paths: list, ledger, client=None) -> PlanExtraction:
    from hwpx.receipts import prepare_image
    content = []
    for n, path in enumerate(paths, 1):
        content += [image_block(jpeg_bytes(prepare_image(Path(path)))), {"type": "text", "text": f"사진 {n}"}]
    result = call(ledger, client, SYSTEM, content, SPEC, effort="high")
    out = to_plan(result.input)
    out.calls.append(result.info)
    return out
