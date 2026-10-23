"""이슈 해결 지원. LLM은 해결 방향·변경안·문장을 제안하고, 코드는
1) 첨부 문서 값을 장부·연결 문서와 대조하고 2) 변경안을 장부 복사본에 적용해(core.apply_resolution) 누계·잔여를
검증한다. 실제 반영은 사용자가 확인한 뒤 호출하는 쪽(app)에서 apply_resolution으로 한다.
"""
import copy
import json
from dataclasses import dataclass, field

from agents.llm import CallInfo, arr, call, i, nullable, obj, s, tool
from core.issues import IssueBook, ResolutionResult, apply_resolution, cumulative_reconciliation
from core.ledger import LEDGER_COLUMNS, LEDGER_INT_COLUMNS, BlockedError, Ledger, parse_date
from core.models import Issue, LedgerChange, LedgerRow, Resolution

KINDS = ["정정", "설명", "추가 확인 필요"]
EDITABLE = [c for c in LEDGER_COLUMNS if c != "순번"]

SYSTEM = """너는 성당 초등부 교사회 회계 장부의 이슈(오류·이상 사항) 해결을 돕는다.
입력: 이슈, 관련 장부 행과 연결 문서, 프로그램이 계산한 사실(누계·잔여·예산 영향), 사용자 설명, 첨부 문서 값과 대조 결과.

원칙
- 금액·누계·잔여는 직접 계산하지 말고 [계산된 사실]의 숫자만 쓴다. 변경안은 프로그램이 다시 검증한다.
- 해결 유형
  - 정정: 근거(사용자 설명 또는 첨부 문서)로 장부 값을 고치거나 빠진 행(예: 누락 보고서)을 추가한다.
  - 설명: 값은 그대로 두고 사유를 기록한다. 필요하면 상태 열만 바꾼다(예: 철회된 신청서 → 취소).
  - 추가 확인 필요: 판단할 근거가 부족하다. 무엇이 더 필요한지 추가질문에 구체적으로 적는다.
- 사용자 설명이 모호하거나("잘 모르겠어" 등) 첨부가 이슈와 무관하면 추가 확인 필요로 답한다.
- 변경안은 이슈 대상 순번의 행만 바꾼다. 같은 사유로 함께 닫을 열린 이슈가 있으면 함께해결에 넣는다.
- 설명문: 회계담당에게 보여줄 쉬운 말 3~4문장. 무엇이 문제인지, 왜 중요한지(예산 영향 금액), 무엇을 확인해야 하는지.
- 해결내용: 이슈에 남길 한두 문장(무엇을 근거로 어떻게 처리했는지)."""

ROW_FIELDS = {c: nullable(i()) if c in LEDGER_INT_COLUMNS else s() for c in LEDGER_COLUMNS if c != "순번"}

SPEC = tool("submit_issue_assessment", "이슈 해결 제안 제출", obj({
    "해결유형": {"type": "string", "enum": KINDS},
    "변경안": arr(obj({"순번": i(), "열": {"type": "string", "enum": EDITABLE},
                       "변경후": nullable(s("새 값(숫자도 문자열로)")), "사유": s()})),
    "추가행": arr(obj(ROW_FIELDS)),
    "함께해결": arr(s("이슈ID")),
    "해결내용": s(),
    "남은의문": arr(s()),
    "추가질문": arr(s()),
    "설명문": s(),
}))


@dataclass
class IssueAssist:
    issue: Issue
    해결유형: str
    resolution: Resolution | None
    diff: list[dict] = field(default_factory=list)          # 필드별 전후 값(검증 시뮬레이션 결과)
    verification: ResolutionResult | None = None
    추가질문: list[str] = field(default_factory=list)
    남은의문: list[str] = field(default_factory=list)
    설명문: str = ""
    첨부대조: list[dict] = field(default_factory=list)       # 첨부 문서 값 ↔ 장부 값 차이
    problems: list[str] = field(default_factory=list)        # 변경안을 받아들이지 못한 이유
    calls: list[CallInfo] = field(default_factory=list)


# ---------------------------------------------------------------- 사실 모으기(코드)

def _row_text(r: LedgerRow) -> str:
    return json.dumps({k: v for k, v in r.model_dump().items() if v not in (None, "")}, ensure_ascii=False)


def related_rows(ledger: Ledger, issue: Issue) -> list[LedgerRow]:
    rows = [ledger.row(n) for n in issue.대상순번]
    linked = [ledger.find_ref(r.연결문서번호) for r in rows if r.연결문서번호]
    return rows + [r for r in linked if r and r not in rows]


def budget_facts(ledger: Ledger, book: IssueBook, issue: Issue) -> dict:
    rec = cumulative_reconciliation(ledger, book)
    s_ = rec.summary
    impact = {item["순번"]: item["차이"] for item in rec.항목 if item["순번"] in issue.대상순번}
    facts = {
        "문서기준_누계": s_.문서기준, "재계산_누계": s_.재계산, "누계_차이": s_.차이,
        "문서기준_잔여": s_.문서기준_잔여, "재계산_잔여": s_.재계산_잔여,
        "대상순번별_누계_단절": impact,
    }
    for r in (ledger.row(n) for n in issue.대상순번):
        if r.신청서 and not r.취소:
            facts.setdefault("대상_신청액", {})[r.순번] = r.금번신청액
        if r.사업ID in ledger.plan:
            facts.setdefault("사업_한도", {})[r.사업ID] = {
                "계획": ledger.plan[r.사업ID].본당예산, "실집행": ledger.business_spent(r.사업ID),
                "남은한도": ledger.business_limit(r.사업ID)}
    return facts


ATTACH_MAP = {  # 장부 열 ← 첨부 문서 필드
    "예산신청서": {"문서번호": "문서번호", "행사명": "행사명", "예산배정액": "예산배정액", "금번신청액": "금번신청액",
              "신청누계액": "신청누계액", "잔여액": "잔여액", "자체예산": "자체예산", "본당보조": "본당보조",
              "합계": "합계"},
    "집행보고서": {"문서번호": "문서번호", "행사명": "행사명", "자체예산": "자체예산", "본당보조": "본당보조",
              "합계": "합계B", "잔액": "잔액"},
}


def compare_attachment(ledger: Ledger, issue: Issue, attachment) -> tuple[LedgerRow | None, list[dict]]:
    """첨부(hwpx.reader.ReadResult)를 문서번호로 장부 행과 맞추고 값 차이를 돌려준다."""
    doc = attachment.doc
    candidates = related_rows(ledger, issue)
    row = next((r for r in candidates if r.문서번호 == doc.문서번호 and r.구분 == attachment.kind), None) \
        or next((r for r in candidates if r.구분 == attachment.kind and r.순번 in issue.대상순번), None)
    if row is None:
        return None, []
    diffs = []
    if doc.작성일 and parse_date(row.작성일) != doc.작성일:
        diffs.append({"순번": row.순번, "열": "작성일", "장부": row.작성일, "첨부": doc.작성일.isoformat()})
    blank = lambda v: None if v in (None, "") else v
    for col, attr in ATTACH_MAP[attachment.kind].items():
        a, b = blank(getattr(row, col)), blank(getattr(doc, attr))
        if a != b:
            diffs.append({"순번": row.순번, "열": col, "장부": a, "첨부": b})
    detail = sum(x.금액 or x.단가 * x.수량 for x in doc.세부내역)
    total = getattr(doc, "합계B", None) if attachment.kind == "집행보고서" else getattr(doc, "합계", None)
    if doc.세부내역 and total is not None and detail != total:
        diffs.append({"순번": row.순번, "열": "(첨부 세부내역 합)", "장부": total, "첨부": detail})
    return row, diffs


# ---------------------------------------------------------------- 변경안 검증(코드)

def _typed(col: str, value):
    if col in LEDGER_INT_COLUMNS:
        if value in (None, ""):
            return None
        return int(str(value).replace(",", "").replace("원", ""))
    return "" if value is None else str(value)


def to_resolution(out: dict, issue: Issue, book: IssueBook, 처리자: str) -> tuple[Resolution | None, list[str]]:
    problems = []
    kind = out["해결유형"]
    if kind not in ("정정", "설명"):
        return None, problems
    changes = []
    for c in out.get("변경안") or []:
        if c["순번"] not in issue.대상순번:
            problems.append(f"순번 {c['순번']}은 이슈 대상이 아니라 제외")
            continue
        if kind == "설명" and c["열"] != "상태":
            problems.append(f"설명 처리는 상태 열만 바꿀 수 있어 '{c['열']}' 변경 제외")
            continue
        try:
            changes.append(LedgerChange(순번=c["순번"], 열=c["열"], 값=_typed(c["열"], c["변경후"])))
        except ValueError:
            problems.append(f"순번 {c['순번']} {c['열']} 값 '{c['변경후']}'이 숫자가 아님")
    rows = []
    for r in out.get("추가행") or []:
        if kind != "정정":
            problems.append("설명 처리에는 행을 추가할 수 없어 제외")
            break
        try:
            rows.append(LedgerRow(**{k: _typed(k, v) for k, v in r.items()}))
        except Exception as e:
            problems.append(f"추가행을 만들 수 없음: {e}")
    together = [x for x in out.get("함께해결") or [] if x != issue.이슈ID]
    open_ids = {i.이슈ID for i in book.open_issues()}
    for x in [x for x in together if x not in open_ids]:
        problems.append(f"함께해결 {x}는 열린 이슈가 아니라 제외")
    together = [x for x in together if x in open_ids]
    if kind == "정정" and not (changes or rows):
        problems.append("정정인데 장부 변경안이 없음")
        return None, problems
    return Resolution(방식=kind, 내용=out.get("해결내용") or "", 처리자=처리자,
                      변경=changes, 추가행=rows, 함께해결=together), problems


def dry_run(ledger: Ledger, book: IssueBook, issue: Issue, res: Resolution) -> ResolutionResult:
    """장부·이슈 복사본에 적용해 본다(원본은 그대로)."""
    l2 = Ledger([r.model_copy() for r in ledger.rows], ledger.settings.model_copy(deep=True),
                list(ledger.plan.values()), ledger.data_dir)
    b2 = IssueBook(copy.deepcopy(book.issues), list(book.history), book.data_dir)
    return apply_resolution(l2, b2, issue.이슈ID, res)


# ---------------------------------------------------------------- 호출

def assist_issue(issue_id: str, ledger: Ledger, book: IssueBook, user_text: str, attachment=None,
                 처리자: str = "회계관리자", client=None) -> IssueAssist:
    issue = book.get(issue_id)
    rows = related_rows(ledger, issue)
    related = {x.이슈ID: x for x in [book.get(x) for x in issue.관련이슈] + [
        i for i in book.open_issues() if i is not issue and set(i.대상순번) & set(issue.대상순번)]}
    att_row, att_diffs = (compare_attachment(ledger, issue, attachment) if attachment else (None, []))

    parts = [
        f"[이슈]\n{json.dumps(issue.model_dump(mode='json'), ensure_ascii=False)}",
        "[관련 장부 행·연결 문서]\n" + "\n".join(_row_text(r) for r in rows),
        "[같은 행의 다른 이슈]\n" + ("\n".join(f"{i.이슈ID} {i.유형} {i.상태}: {i.설명}" for i in related.values())
                                 or "(없음)"),
        f"[계산된 사실]\n{json.dumps(budget_facts(ledger, book, issue), ensure_ascii=False)}",
        f"[사용자 설명]\n{user_text or '(없음)'}",
    ]
    if attachment is not None:
        d = attachment.doc
        parts.append(f"[첨부 문서({attachment.kind})]\n" + json.dumps(
            d.model_dump(mode="json", exclude={"기안자연락처", "기안단체장연락처", "은행명", "계좌번호", "예금주",
                                               "계좌_override"}), ensure_ascii=False))
        parts.append("[첨부 읽기 실패 칸]\n" + (", ".join(f.칸 for f in attachment.failures) or "(없음)"))
        parts.append(f"[첨부 ↔ 장부 대조(프로그램 계산)] 대상 순번 {att_row.순번 if att_row else '없음'}\n"
                     + (json.dumps(att_diffs, ensure_ascii=False) if att_diffs else "(차이 없음)"))

    answer = call(ledger, client, SYSTEM, "\n\n".join(parts), SPEC, effort="high")
    out = answer.input
    res, problems = to_resolution(out, issue, book, 처리자)
    result = IssueAssist(issue, out["해결유형"], res, 추가질문=list(out.get("추가질문") or []),
                         남은의문=list(out.get("남은의문") or []), 설명문=out.get("설명문") or "",
                         첨부대조=att_diffs, problems=problems, calls=[answer.info])
    if res is None:
        if out["해결유형"] != "추가 확인 필요":
            result.해결유형 = "추가 확인 필요"
            result.추가질문 += [p for p in problems] or ["장부 변경안을 구체적으로 알려 주세요."]
        return result
    try:
        result.verification = dry_run(ledger, book, issue, res)
        result.diff = result.verification.diff
    except BlockedError as e:
        result.해결유형 = "추가 확인 필요"
        result.resolution = None
        detail = "; ".join(f.설명 for f in e.findings)
        result.problems.append(f"검증 실패: {e}{' — ' + detail if detail else ''}")
        result.추가질문.append("제안된 변경으로는 장부 검증을 통과하지 못합니다. 근거 자료(문서 값)를 확인해 주세요.")
    return result
