"""집행보고서 초안. LLM은 영수증 묶기(계정과목·내용)와 문장만 정하고,
금액은 영수증 값의 합으로 코드가 채운다. 보고서 금액(본당·자체·잔액)은 core.report.build_report가 계산한다.

경과보고 한 줄 형식: "M월 D일 항목 : 금액원 / 참가자 : 이름 세례명, …"
"""
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from agents.llm import CallInfo, arr, call, i, nullable, obj, s, tool
from agents.receipt_agent import ReceiptExtraction, duplicate_warnings
from core.models import LineItem, Settings
from core.paths import TEMPLATES_DIR

ACCOUNTS = "식대, 다과비, 간식비, 교재비, 소모품비, 행사용품비, 선물비, 보험료, 차량임차료, 교통비, 장소대여비, 인쇄비, 행사비"

FORMAT_EXAMPLE = """8월 교사회 운영비를 다음과 같이 사용하였습니다.
8월 15일 미사 후 식대 : 292,100원 / 참가자 : 홍길동 베드로, 김영희 마리아
8월 16일 회합 후 다과 : 43,300원 / 참가자 : 홍길동 베드로"""

SYSTEM = """너는 천주교 개포동성당 초등부 교사회의 예산집행보고서(서식2) 초안을 쓴다.
입력: 회계담당이 자연어로 적은 행사·목적·일시·인원(비어 있을 수 있음), 올해 사업계획(시기 | 사업명 | 세부내역),
영수증 목록(번호·날짜·상호·금액·품목), 경과보고 메모(progress_note, 없을 수 있음).

할 일
1. 세부내역: 영수증을 계정과목별로 묶는다. 계정과목은 지출 내용을 보고 회계 용어로 정한다({accounts} 등).
   각 영수증 번호는 세부내역 중 정확히 한 곳에만 넣는다. 금액은 적지 않는다(프로그램이 영수증 금액을 더한다).
2. 경과보고 줄: 한 줄에 한 건(같은 날 같은 용도는 한 줄). 날짜("M월 D일")와 항목(예: "미사 후 식대")과
   해당 영수증 번호, 참가자를 정한다. 금액은 적지 않는다.
   - 행사·목적에서 맥락(무슨 행사인지, 누구를 위해 왜 썼는지)을 판단해 머리말과 각 줄의 항목을 그 행사에 맞게 쓴다.
     예: 행사 "10월 교리 후 간식" → 머리말 "10월 초등부 교리 간식비를 다음과 같이 사용하였습니다.", 항목 "교리 후 어린이 간식 구입".
     행사·목적이 없으면 영수증만 보고 쓴다.
   - 사업계획에서 행사·목적·영수증(날짜·품목)에 맞는 사업을 하나 고르고, 그 사업명·시기·세부내역의 맥락도 머리말·항목에
     반영한다(예: 사업 "부서 소풍(전례부, 성가대)", 행사 "전례부 소풍" → "10월 전례부 소풍 비용을 다음과 같이 사용하였습니다.").
     고른 사업명은 사업명_참고에 사업계획 표기 그대로 적는다. 맞는 사업이 없으면 사업명_참고는 null.
   - progress_note가 있으면 그 내용을 기준으로 줄을 만든다. 노트에 적힌 날짜·금액은 노트_날짜·노트_금액에 그대로 옮긴다.
     노트에서 나온 줄은 노트_언급=true. 노트에 없는 영수증으로 줄을 만들 때는 노트_언급=false.
   - progress_note가 없으면 영수증만으로 줄을 만든다(노트_언급=false, 노트_날짜·노트_금액 null).
   - 참가자는 노트에 적힌 사람만, 적힌 그대로 옮긴다. 명단에서 짐작해 추가하지 않는다.
   - 머리말은 "N월 ○○을 다음과 같이 사용하였습니다." 같은 한 문장.
3. 노트·행사 정보와 영수증이 서로 맞지 않는 점이 보이면 불일치_메모에 짧게 적는다.
4. [대행사 표기]인 영수증은 가맹점명이 결제대행사라 실제 가게가 아니다. progress_note에 그 결제의 실제 상호가
   보이면 대행사_상호에 {{영수증번호, 상호}}로 적는다(노트에 없으면 적지 않는다). 세부내역 내용에도 실제 상호·용도를 쓴다.

교사 명단(이름 세례명): {roster}

경과보고 형식 예시
{examples}"""

SPEC = tool("submit_report_draft", "집행보고서 초안 제출", obj({
    "머리말": s(),
    "경과보고_줄": arr(obj({
        "날짜": nullable(s("M월 D일")),
        "항목": s(),
        "영수증번호": arr(i()),
        "참가자": arr(s()),
        "노트_언급": {"type": "boolean"},
        "노트_날짜": nullable(s("노트에 적힌 날짜 그대로")),
        "노트_금액": nullable(i("노트에 적힌 금액(원)")),
    })),
    "세부내역": arr(obj({"계정과목": s(), "내용": s(), "영수증번호": arr(i())})),
    "불일치_메모": arr(s()),
    "사업명_참고": nullable(s("사업계획에서 고른 사업명(표기 그대로), 없으면 null")),
    "대행사_상호": arr(obj({"영수증번호": i(), "상호": s()}), "대행사 표기 영수증의 실제 상호(노트에 있을 때만)"),
}))


@lru_cache(maxsize=1)
def example_reports() -> tuple[str, ...]:
    """templates/examples/의 실제 보고서 경과보고(있을 때만, 커밋 제외 파일)."""
    from hwpx.reader import read_document
    out = []
    for path in sorted((TEMPLATES_DIR / "examples").glob("report_*.hwpx")):
        try:
            text = read_document(path).doc.경과보고.strip()
        except Exception:
            continue
        if text:
            out.append(text)
    return tuple(out)


# ---------------------------------------------------------------- 결과

@dataclass
class ReportLine:
    날짜: str
    항목: str
    금액: int
    참가자: list[str]
    영수증번호: list[int]

    def text(self) -> str:
        head = f"{self.날짜} {self.항목}".strip()
        line = f"{head} : {self.금액:,}원"
        return line + (f" / 참가자 : {', '.join(self.참가자)}" if self.참가자 else "")


@dataclass
class ReportComposition:
    세부내역: list[LineItem]
    경과보고: str
    lines: list[ReportLine]
    warnings: list[str] = field(default_factory=list)
    참가자_선택: list[dict] = field(default_factory=list)  # 세례명이 겹쳐 고를 사람: {줄, 입력, 후보}
    calls: list[CallInfo] = field(default_factory=list)
    사업명: str | None = None  # Claude가 사업계획에서 고른 사업(참고용)


def md(d: date) -> str:
    return f"{d.month}월 {d.day}일"


def parse_md(text: str | None, year: int) -> date | None:
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text or "") or \
        re.search(r"\d{4}-(\d{1,2})-(\d{1,2})", text or "")
    try:
        return date(year, int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


def participant_candidates(name: str, roster: list[str]) -> list[str]:
    """'정연재', '정연재 레오', '레오' → 맞는 명단 표기들. 하나면 확정, 둘 이상이면 사용자가 고른다."""
    name = re.sub(r"\s+", " ", name).strip()
    if name in roster:
        return [name]
    parts = name.split()
    by_name = [r for r in roster if r.split()[0] == parts[0]
               and (len(parts) == 1 or parts[1:] == r.split()[1:])]
    if by_name:
        return by_name
    by_saint = [r for r in roster if " ".join(r.split()[1:]) == name]
    if by_saint:
        return by_saint
    # 성 없이 이름만('연재' → '정연재 레오'): 세 글자 이상 이름의 뒤 글자와 같을 때
    return [r for r in roster if len(parts) == 1 and len(r.split()[0]) >= 3 and r.split()[0][1:] == parts[0]]


def match_participant(name: str, roster: list[str]) -> str | None:
    """명단에서 한 사람으로 정해지면 그 표기, 없거나 여럿이면 None."""
    hits = participant_candidates(name, roster)
    return hits[0] if len(hits) == 1 else None


def receipt_note(nos: list[int]) -> str:
    """세부내역 비고: 첨부 docx의 영수증 번호(예: '영수증 1, 3')."""
    return "영수증 " + ", ".join(str(n) for n in sorted(nos))


def compose(out: dict, receipts: list[ReceiptExtraction], roster: list[str], year: int,
            has_note: bool) -> ReportComposition:
    by_no = {r.순번: r for r in receipts}
    warnings = duplicate_warnings(receipts)
    for item in out.get("대행사_상호") or []:  # 사용자 설명의 상호를 우선
        r = by_no.get(item.get("영수증번호"))
        if r and r.대행사표기 and item.get("상호") and not r.상호_사용자:
            r.상호_사용자 = item["상호"]
    for r in receipts:
        if r.대행사표기 and not r.상호_사용자:
            warnings.append(f"영수증 {r.순번}번 가맹점명이 결제대행사({r.가맹점명_원문 or r.상호}) — 실제 상호를 입력해 주세요")
        if r.확인필요 and not (r.대행사표기 and r.상호_사용자 and r.금액 is not None and r.날짜):
            warnings.append(f"영수증 {r.순번}번({r.상호_확정 or r.파일}) 확인 필요: {r.확인사유}")

    def amount(nos: list[int]) -> int:
        return sum(by_no[n].금액 or 0 for n in nos if n in by_no)

    # 세부내역: 영수증은 정확히 한 번
    used, items = set(), []
    for it in out.get("세부내역") or []:
        nos = []
        for n in it.get("영수증번호") or []:
            if n not in by_no:
                warnings.append(f"세부내역 '{it['내용']}'의 영수증 {n}번은 없는 번호")
            elif n in used:
                warnings.append(f"영수증 {n}번이 세부내역 두 곳에 들어가 한 번만 반영")
            else:
                nos.append(n)
                used.add(n)
        if nos:
            items.append(LineItem(계정과목=it.get("계정과목") or "", 내용=it["내용"], 단가=amount(nos), 수량=1,
                                  비고=receipt_note(nos)))
    for r in receipts:
        if r.순번 not in used:
            warnings.append(f"영수증 {r.순번}번({r.상호})이 세부내역에 없어 따로 넣음")
            items.append(LineItem(계정과목="기타", 내용=r.품목요약 or r.상호, 단가=r.금액 or 0, 수량=1,
                                  비고=receipt_note([r.순번])))

    # 경과보고 줄
    lines, mentioned, choices = [], set(), []
    for ln in out.get("경과보고_줄") or []:
        nos = [n for n in ln.get("영수증번호") or [] if n in by_no]
        dates = sorted({by_no[n].날짜 for n in nos if by_no[n].날짜})
        label = ln.get("날짜") or (md(dates[0]) if dates else "")
        people = []
        for p in ln.get("참가자") or []:
            hits = participant_candidates(p, roster)
            if len(hits) > 1:
                choices.append({"줄": f"{label} {ln['항목']}", "입력": p, "후보": hits})
                warnings.append(f"참가자 확인 필요: '{p}' → 후보 {', '.join(hits)} ({label} {ln['항목']})")
            elif not hits:
                warnings.append(f"명단에 없는 참가자: '{p}' ({label} {ln['항목']})")
            people.append(hits[0] if len(hits) == 1 else p)
        total = amount(nos)
        if ln.get("노트_언급"):
            mentioned.update(nos)
            if not nos:
                warnings.append(f"경과보고 '{label} {ln['항목']}'에 맞는 영수증이 없음")
            if ln.get("노트_금액") is not None and nos and ln["노트_금액"] != total:
                warnings.append(f"금액 불일치: '{label} {ln['항목']}' 노트 {ln['노트_금액']:,}원 / 영수증 {total:,}원")
            noted = parse_md(ln.get("노트_날짜"), year)
            if noted and dates and noted not in dates:
                warnings.append(f"날짜 불일치: '{ln['항목']}' 노트 {md(noted)} / 영수증 "
                                + ", ".join(md(d) for d in dates))
        lines.append(ReportLine(label, ln["항목"], total, people, nos))
    if has_note:
        for r in receipts:
            if r.순번 not in mentioned:
                won = f" {r.금액:,}원" if r.금액 is not None else ""
                warnings.append(f"progress_note에 없는 영수증: {r.순번}번 {r.상호}{won}")
    warnings += [f"검토 메모: {m}" for m in out.get("불일치_메모") or []]

    body = "\n".join([out.get("머리말") or "", *(ln.text() for ln in lines)]).strip()
    return ReportComposition(items, body, lines, warnings, choices, 사업명=out.get("사업명_참고") or None)


# ---------------------------------------------------------------- 호출

@lru_cache(maxsize=4)
def _system(roster: tuple[str, ...]) -> str:
    examples = "\n\n".join([FORMAT_EXAMPLE, *example_reports()])
    return SYSTEM.format(accounts=ACCOUNTS, roster=", ".join(roster), examples=examples)


def _receipt_lines(receipts: list[ReceiptExtraction]) -> str:
    return "\n".join(
        f"{r.순번}. {r.날짜.isoformat() if r.날짜 else '날짜 미상'} | {r.상호_확정 or '상호 미상'}"
        f"{' [대행사 표기]' if r.대행사표기 and not r.상호_사용자 else ''} | "
        f"{f'{r.금액:,}원' if r.금액 is not None else '금액 미상'} | {r.품목요약}" for r in receipts)


def _plan_lines(plan: list[dict] | None) -> str:
    return "\n".join(f"{p['시기']} | {p['사업명']} | {p['세부내역']}" for p in plan or []) or "(없음)"


def ask(settings: Settings, event: str, receipts, progress_note, client, plan: list[dict] | None = None):
    """event: 회계담당이 적은 행사 정보 줄. plan: core.report.load_plan(시기·사업명·세부내역). 비어 있을 수 있다."""
    text = (f"[행사 정보 — 회계담당 입력]\n{event or '(없음)'}\n\n[사업계획 — 시기 | 사업명 | 세부내역]\n{_plan_lines(plan)}\n\n"
            f"[영수증]\n{_receipt_lines(receipts)}\n\n[progress_note]\n{progress_note or '(없음)'}")
    return call(settings, client, _system(tuple(settings.교사명단)), text, SPEC, effort="high")


def compose_report(settings: Settings, event: str, receipts: list[ReceiptExtraction],
                   progress_note: str | None = None, client=None, plan: list[dict] | None = None) -> ReportComposition:
    result = ask(settings, event, receipts, progress_note, client, plan)
    comp = compose(result.input, receipts, settings.교사명단, settings.현재연도, bool(progress_note))
    comp.calls.append(result.info)
    return comp
