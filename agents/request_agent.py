"""자연어 → RequestDoc 초안. LLM은 정보만 뽑고, 금액·누계·번호는 core.build_request가 계산한다.

예: "12월 20일 성탄 행사, 어린이 120명, 간식 3000원씩, 선물 5000원씩"
"""
from dataclasses import dataclass, field
from datetime import date, datetime

from agents.llm import CallInfo, arr, call, i, nullable, obj, s, tool
from core.ledger import Ledger
from core.models import LineItem, RequestDoc

EVENT_TYPES = ["행사·교육·피정", "성지순례·친교·기타"]
REQUIRED = ("행사명", "대상월", "시작일", "세부항목")

SYSTEM = """너는 천주교 서울대교구 개포동성당 초등부 교사회의 예산신청서(서식1) 작성을 돕는다.
사용자의 짧은 설명에서 신청서 칸에 들어갈 정보만 뽑는다.

규칙
- 적혀 있지 않은 정보는 추측하지 말고 비워 두고(숫자는 null, 글자는 빈 문자열), 그 항목 이름을 missing에 넣는다.
  (장소·시각·참가대상은 비워 두면 프로그램이 설정 기본값으로 채운다. 이 경우에도 missing에는 넣지 않는다.)
- 금액 합계·누계는 계산하지 않는다. 세부항목에는 단가와 수량만 적는다.
  "간식 3000원씩, 어린이 120명"이면 단가 3000, 수량 120이다. 수량을 알 수 없으면 그 항목 수량은 null.
- 날짜의 연도가 없으면 회계연도 {year}년으로 본다. 대상 월은 행사가 속한 달이다.
- 계정과목은 회계 용어로 짧게: 식대, 다과비, 간식비, 선물비, 교재비, 소모품비, 보험료, 차량임차료, 장소대여비, 행사비 등.
- 사업ID 후보는 아래 사업계획에서 고르고, 고른 근거를 짧게 적는다. 맞는 사업이 없으면 빈 목록.
- 행사일정은 신청서 '행사 일정' 칸에 들어갈 2~4줄 문장이다. 사용자가 준 사실만으로 쓴다.

사업계획(사업ID | 사업명 | 세부내역 | 시기 | 본당예산)
{plan}

오늘: {today}"""

# strict 스키마는 null 허용(union) 속성이 16개까지라, 글자 칸은 null 대신 빈 문자열("")로 '없음'을 표시한다.
SPEC = tool("submit_request_draft", "예산신청서 초안 정보 제출", obj({
    "행사명": s("없으면 빈 문자열"),
    "대상월": nullable(i("1~12")),
    "행사유형": {"type": "string", "enum": [*EVENT_TYPES, ""], "description": "모르면 빈 문자열"},
    "목적": s("없으면 빈 문자열"),
    "시작일": s("YYYY-MM-DD, 없으면 빈 문자열"),
    "시작시각": s("HH:MM, 적혀 있을 때만. 없으면 빈 문자열"),
    "종료일": s("YYYY-MM-DD, 없으면 빈 문자열"),
    "종료시각": s("HH:MM, 적혀 있을 때만. 없으면 빈 문자열"),
    "박": nullable(i()),
    "일": nullable(i()),
    "장소": s("없으면 빈 문자열"),
    "참가대상": s("없으면 빈 문자열"),
    "인원_총": nullable(i()),
    "인원_남": nullable(i()),
    "인원_여": nullable(i()),
    "세부항목": arr(obj({
        "계정과목": s(), "내용": s(), "단가": nullable(i()), "수량": nullable(i()),
    })),
    "사업ID_후보": arr(obj({"사업ID": s(), "근거": s()})),
    "행사일정": s(),
    "missing": arr(s(), "사용자 설명에 없어 채우지 못한 항목 이름"),
}))


@dataclass
class RequestDraft:
    doc: RequestDoc
    missing: list[str] = field(default_factory=list)      # 사용자에게 물어야 할 항목
    defaulted: list[str] = field(default_factory=list)    # 설정 기본값으로 채운 항목
    candidates: list[dict] = field(default_factory=list)  # 사업ID 후보 [{사업ID, 근거, 사업명}]
    raw: dict = field(default_factory=dict)
    calls: list[CallInfo] = field(default_factory=list)   # 호출 기록(실제 응답 모델·시간·토큰)


def _plan_table(ledger: Ledger) -> str:
    return "\n".join(f"{p.사업ID} | {p.사업명} | {p.세부내역} | {p.시기} | {p.본당예산:,}원"
                     for p in ledger.plan.values())


def _date(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _time(v, default: str) -> tuple[int, int, bool]:
    """(시, 분, 기본값 사용 여부)."""
    raw = v or default
    try:
        h, m = (int(x) for x in raw.split(":"))
    except ValueError:
        h, m = (int(x) for x in default.split(":"))
        return h, m, True
    return h, m, not v


def to_draft(out: dict, ledger: Ledger) -> RequestDraft:
    """LLM 추출 결과 → RequestDoc 초안. 기본값 채우기와 누락 판정은 코드가 한다."""
    s_ = ledger.settings
    rules, base = s_.금액문서기준, s_.기본정보
    missing = list(dict.fromkeys(out.get("missing") or []))
    defaulted = []

    start_day, end_day = _date(out.get("시작일")), _date(out.get("종료일"))
    start = end = None
    if start_day:
        h, m, used = _time(out.get("시작시각"), rules.기본시작시각)
        start = datetime(start_day.year, start_day.month, start_day.day, h, m)
        if used:
            defaulted.append("시작시각")
        end_day = end_day or start_day
        h, m, used = _time(out.get("종료시각"), rules.기본종료시각)
        end = datetime(end_day.year, end_day.month, end_day.day, h, m)
        if used:
            defaulted.append("종료시각")
    for key, default in (("장소", base.기본장소), ("참가대상", base.참가대상)):
        if not out.get(key):
            out[key] = default
            defaulted.append(key)

    items, incomplete = [], []
    for n, it in enumerate(out.get("세부항목") or [], 1):
        if it.get("단가") is None or it.get("수량") is None:
            incomplete.append(f"세부항목{n}({it.get('내용', '')}) " + ("단가" if it.get("단가") is None else "수량"))
            continue
        items.append(LineItem(계정과목=it.get("계정과목") or "", 내용=it["내용"],
                              단가=int(it["단가"]), 수량=int(it["수량"])))
    missing += incomplete

    candidates = [{**c, "사업명": ledger.plan[c["사업ID"]].사업명}
                  for c in out.get("사업ID_후보") or [] if c.get("사업ID") in ledger.plan]
    month = out.get("대상월") or (start.month if start else None)
    checks = {"행사명": out.get("행사명"), "대상월": month, "시작일": start, "세부항목": items,
              "사업ID": candidates}
    missing += [k for k, v in checks.items() if not v and k not in missing]

    doc = RequestDoc(
        월=month or 0, 사업ID=candidates[0]["사업ID"] if candidates else "",
        행사명=out.get("행사명") or "", 행사유형=out.get("행사유형") or EVENT_TYPES[0],
        목적=out.get("목적") or "", 시작=start, 종료=end, 박=out.get("박"), 일=out.get("일"),
        장소=out["장소"], 참가대상=out["참가대상"], 인원_총=out.get("인원_총"),
        인원_남=out.get("인원_남"), 인원_여=out.get("인원_여"),
        행사일정=out.get("행사일정") or "", 세부내역=items,
    )
    for key in ("인원_남", "인원_여"):
        if getattr(doc, key) is None and key not in missing:
            missing.append(key)
    return RequestDraft(doc, missing, defaulted, candidates, out)


def extract_request(text: str, ledger: Ledger, today: date | None = None, client=None) -> RequestDraft:
    today = today or date.today()
    system = SYSTEM.format(year=ledger.year, plan=_plan_table(ledger),
                           today=today.isoformat())
    result = call(ledger, client, system, text, SPEC)
    draft = to_draft(result.input, ledger)
    draft.calls.append(result.info)
    return draft
