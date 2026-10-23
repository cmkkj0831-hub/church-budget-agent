"""집행보고서 초안. LLM은 영수증 묶기(계정과목·내용)와 문장만 정하고,
금액은 영수증 값의 합으로 코드가 채운다. 보고서 금액(본당·자체·잔액)은 core.build_report가 계산한다.

경과보고 한 줄 형식: "M월 D일 항목 : 금액원 / 참가자 : 이름 세례명, …"
"""
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from agents.llm import CallInfo, arr, call, i, nullable, obj, s, tool
from agents.receipt_agent import ReceiptExtraction, duplicate_warnings
from core.ledger import BuildResult, Ledger
from core.models import LedgerRow, LineItem, ReportDoc, RequestDoc
from core.paths import TEMPLATES_DIR

ACCOUNTS = "식대, 다과비, 간식비, 교재비, 소모품비, 행사용품비, 선물비, 보험료, 차량임차료, 교통비, 장소대여비, 인쇄비, 행사비"

FORMAT_EXAMPLE = """8월 교사회 운영비를 다음과 같이 사용하였습니다.
8월 15일 미사 후 식대 : 292,100원 / 참가자 : 홍길동 베드로, 김영희 마리아
8월 16일 회합 후 다과 : 43,300원 / 참가자 : 홍길동 베드로"""

SYSTEM = """너는 천주교 개포동성당 초등부 교사회의 예산집행보고서(서식2) 초안을 쓴다.
입력: 연결된 예산신청서 정보, 영수증 목록(번호·날짜·상호·금액·품목), 경과보고 메모(progress_note, 없을 수 있음),
차기 계획·건의사항 메모(suggestion, 없을 수 있음).

할 일
1. 세부내역: 영수증을 계정과목별로 묶는다. 계정과목은 지출 내용을 보고 회계 용어로 정한다({accounts} 등).
   각 영수증 번호는 세부내역 중 정확히 한 곳에만 넣는다. 금액은 적지 않는다(프로그램이 영수증 금액을 더한다).
2. 경과보고 줄: 한 줄에 한 건(같은 날 같은 용도는 한 줄). 날짜("M월 D일")와 항목(예: "미사 후 식대")과
   해당 영수증 번호, 참가자를 정한다. 금액은 적지 않는다.
   - progress_note가 있으면 그 내용을 기준으로 줄을 만든다. 노트에 적힌 날짜·금액은 노트_날짜·노트_금액에 그대로 옮긴다.
     노트에서 나온 줄은 노트_언급=true. 노트에 없는 영수증으로 줄을 만들 때는 노트_언급=false.
   - progress_note가 없으면 영수증만으로 줄을 만든다(노트_언급=false, 노트_날짜·노트_금액 null).
   - 참가자는 노트에 적힌 사람만, 적힌 그대로 옮긴다. 명단에서 짐작해 추가하지 않는다.
   - 머리말은 "N월 ○○을 다음과 같이 사용하였습니다." 같은 한 문장.
3. 건의사항: suggestion이 있으면 공문서 말투로 다듬는다. 없으면 빈 문자열.
4. 노트와 영수증이 서로 맞지 않는 점이 보이면 불일치_메모에 짧게 적는다.
5. [대행사 표기]인 영수증은 가맹점명이 결제대행사라 실제 가게가 아니다. progress_note에 그 결제의 실제 상호가
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
    "건의사항": s(),
    "불일치_메모": arr(s()),
    "행사명_제안": nullable(s()),
    "대상월": nullable(i()),
    "사업ID_후보": arr(obj({"사업ID": s(), "근거": s()})),
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
    건의사항: str
    lines: list[ReportLine]
    warnings: list[str] = field(default_factory=list)
    행사명_제안: str | None = None
    대상월: int | None = None
    사업ID_후보: list[dict] = field(default_factory=list)
    참가자_선택: list[dict] = field(default_factory=list)  # 세례명이 겹쳐 고를 사람: {줄, 입력, 후보}
    calls: list[CallInfo] = field(default_factory=list)


@dataclass
class ReportDraft:
    doc: ReportDoc
    build: BuildResult
    composition: ReportComposition

    @property
    def warnings(self) -> list[str]:
        return self.composition.warnings


# ---------------------------------------------------------------- 코드 쪽 정리

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
    return [r for r in roster if " ".join(r.split()[1:]) == name]


def match_participant(name: str, roster: list[str]) -> str | None:
    """명단에서 한 사람으로 정해지면 그 표기, 없거나 여럿이면 None."""
    hits = participant_candidates(name, roster)
    return hits[0] if len(hits) == 1 else None


def compose(out: dict, receipts: list[ReceiptExtraction], roster: list[str], year: int,
            has_note: bool, has_suggestion: bool) -> ReportComposition:
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
            items.append(LineItem(계정과목=it.get("계정과목") or "", 내용=it["내용"], 단가=amount(nos), 수량=1))
    for r in receipts:
        if r.순번 not in used:
            warnings.append(f"영수증 {r.순번}번({r.상호})이 세부내역에 없어 따로 넣음")
            items.append(LineItem(계정과목="기타", 내용=r.품목요약 or r.상호, 단가=r.금액 or 0, 수량=1))

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
    return ReportComposition(items, body, (out.get("건의사항") or "") if has_suggestion else "", lines,
                             warnings, out.get("행사명_제안"), out.get("대상월"),
                             [c for c in out.get("사업ID_후보") or []], choices)


# ---------------------------------------------------------------- 호출

def _system(ledger: Ledger) -> str:
    examples = "\n\n".join([FORMAT_EXAMPLE, *example_reports()])
    return SYSTEM.format(accounts=ACCOUNTS, roster=", ".join(ledger.settings.교사명단), examples=examples)


def _receipt_lines(receipts: list[ReceiptExtraction]) -> str:
    return "\n".join(
        f"{r.순번}. {r.날짜.isoformat() if r.날짜 else '날짜 미상'} | {r.상호_확정 or '상호 미상'}"
        f"{' [대행사 표기]' if r.대행사표기 and not r.상호_사용자 else ''} | "
        f"{f'{r.금액:,}원' if r.금액 is not None else '금액 미상'} | {r.품목요약}" for r in receipts)


def _request_lines(request) -> str:
    if request is None:
        return "(연결 신청서 없음 — 사후정산이면 보고서를 먼저 확정하고 신청서를 역생성한다)"
    keys = ("문서번호", "행사명", "월", "사업ID", "금번신청액", "목적", "행사일정")
    return "\n".join(f"{k}: {getattr(request, k)}" for k in keys if getattr(request, k, None) not in (None, ""))


def ask(ledger: Ledger, request, receipts, progress_note, suggestion, client, extra: str = ""):
    text = (f"[연결 신청서]\n{_request_lines(request)}\n\n[영수증]\n{_receipt_lines(receipts)}\n\n"
            f"[progress_note]\n{progress_note or '(없음)'}\n\n[suggestion]\n{suggestion or '(없음)'}{extra}")
    return call(ledger, client, _system(ledger), text, SPEC, effort="high")


def compose_report(ledger: Ledger, request, receipts: list[ReceiptExtraction], progress_note: str | None = None,
                   suggestion: str | None = None, client=None, extra: str = "") -> ReportComposition:
    result = ask(ledger, request, receipts, progress_note, suggestion, client, extra)
    comp = compose(result.input, receipts, ledger.settings.교사명단, ledger.settings.기본정보.회계연도,
                   bool(progress_note), bool(suggestion))
    comp.calls.append(result.info)
    return comp


def report_fields(request) -> dict:
    """연결 신청서에서 보고서로 옮기는 칸."""
    if isinstance(request, RequestDoc):
        keys = ("월", "사업ID", "행사명", "행사유형", "주관단체", "협조단체", "목적", "시작", "종료", "박", "일",
                "장소", "참가대상", "인원_총", "인원_남", "인원_여")
        return {k: getattr(request, k) for k in keys}
    return {"월": request.월번호, "사업ID": request.사업ID, "행사명": request.행사명}


def draft_report(request: LedgerRow | RequestDoc, receipts: list[ReceiptExtraction], ledger: Ledger,
                 progress_note: str | None = None, suggestion: str | None = None,
                 작성일: date | None = None, client=None, today: date | None = None) -> ReportDraft:
    comp = compose_report(ledger, request, receipts, progress_note, suggestion, client)
    draft = ReportDoc(**report_fields(request), 작성일=작성일, 세부내역=comp.세부내역,
                      경과보고=comp.경과보고, 건의사항=comp.건의사항)
    build = ledger.build_report(draft, request, today=today)
    return ReportDraft(build.docs[0], build, comp)
