"""검증. 오류는 생성을 막고, 경고는 표시한 뒤 사용자가 진행하면 이슈가 된다."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from core.ledger import doc_seq, months_in_name, parse_date
from core.models import Issue, IssueType, LedgerRow, ReportDoc, RequestDoc


class Finding(BaseModel):
    수준: Literal["오류", "경고"]
    코드: str
    유형: IssueType
    설명: str
    순번: list[int] = Field(default_factory=list)
    문서번호: list[str] = Field(default_factory=list)


def _err(code, kind, desc, **kw) -> Finding:
    return Finding(수준="오류", 코드=code, 유형=kind, 설명=desc, **kw)


def _warn(code, kind, desc, **kw) -> Finding:
    return Finding(수준="경고", 코드=code, 유형=kind, 설명=desc, **kw)


def won(n) -> str:
    return "공란" if n is None else f"{n:,}원"


# ---------------------------------------------------------------- 문서 검증

def check_line_items(doc) -> list[Finding]:
    out = []
    for i in doc.세부내역:
        if i.금액 is not None and i.단가 * i.수량 != i.금액:
            out.append(_err("line_amount", "계산 불일치",
                            f"'{i.내용}' 단가×수량 {i.단가 * i.수량:,}원 ≠ 금액 {i.금액:,}원"))
    return out


def check_doc_context(doc, ledger, today: date | None = None) -> list[Finding]:
    """월·연도·사업ID·문서번호 등 문서 밖 기준과의 일치."""
    out = []
    year = ledger.settings.기본정보.회계연도
    no = [doc.문서번호] if doc.문서번호 else []
    if doc.사업ID not in ledger.plan:
        out.append(_err("unknown_business", "기재 오류", f"사업ID '{doc.사업ID}' 없음", 문서번호=no))
    if not doc.문서번호:
        out.append(_err("missing_doc_no", "기재 오류", "문서번호 미기재"))
    if doc.작성일 is None:
        out.append(_err("missing_date", "기재 오류", "작성일 미기재", 문서번호=no))
    elif doc.작성일.year != year:
        out.append(_warn("date_year", "기재 오류",
                         f"작성일 {doc.작성일.year}년 ≠ 회계연도 {year}년", 문서번호=no))
    named = months_in_name(doc.행사명)
    if named and doc.월 not in named:
        out.append(_warn("name_month", "기재 오류",
                         f"행사명 '{doc.행사명}'의 월이 대상 월({doc.월}월)과 다름", 문서번호=no))
    if doc.시작 is not None:
        if doc.시작.month != doc.월:
            out.append(_warn("period_month", "기재 오류",
                             f"일시 시작 {doc.시작.month}월 ≠ 대상 월 {doc.월}월", 문서번호=no))
        if doc.시작.year != year:
            out.append(_warn("period_year", "기재 오류",
                             f"일시 {doc.시작.year}년 ≠ 회계연도 {year}년", 문서번호=no))
    if doc.시작 and doc.종료 and doc.종료 < doc.시작:
        out.append(_err("period_order", "기재 오류", "일시 종료가 시작보다 이름", 문서번호=no))
    return out


def check_doc_numbers(docs, ledger) -> list[Finding]:
    """새 문서번호가 장부·같은 묶음 안에서 겹치는지. (예)/(집)은 일련번호를 공유한다."""
    used = {}
    for r in ledger.rows:
        if (n := doc_seq(r.문서번호)) is not None:
            used.setdefault(n, []).append(r)
    out, seen = [], set()
    for d in docs:
        n = doc_seq(d.문서번호)
        if n is None:
            continue
        if n in used or n in seen:
            out.append(_err("dup_doc_no", "번호 중복", f"문서번호 {n:03d} 중복",
                            순번=[r.순번 for r in used.get(n, [])], 문서번호=[d.문서번호]))
        seen.add(n)
    return out


def validate_request(doc: RequestDoc, ledger, today: date | None = None) -> list[Finding]:
    out = check_line_items(doc)
    no = [doc.문서번호] if doc.문서번호 else []
    detail = doc.세부합()
    if doc.합계 is None:
        out.append(_err("missing_total", "기재 오류", "합계란 공란", 문서번호=no))
    else:
        if doc.세부내역 and detail != doc.합계:
            out.append(_err("detail_sum", "계산 불일치",
                            f"세부내역 합 {won(detail)} ≠ 합계 {won(doc.합계)}", 문서번호=no))
        if (doc.자체예산 or 0) + (doc.본당보조 or 0) != doc.합계:
            out.append(_err("income_sum", "계산 불일치",
                            f"자체 {won(doc.자체예산 or 0)} + 본당 {won(doc.본당보조)} ≠ 합계 {won(doc.합계)}",
                            문서번호=no))
    if doc.금번신청액 != doc.본당보조:
        out.append(_err("request_vs_subsidy", "계산 불일치",
                        f"금번신청액 {won(doc.금번신청액)} ≠ 본당보조 {won(doc.본당보조)}", 문서번호=no))
    if None not in (doc.예산배정액, doc.신청누계액, doc.잔여액) and \
            doc.예산배정액 - doc.신청누계액 != doc.잔여액:
        out.append(_err("remaining", "계산 불일치",
                        f"잔여액 {won(doc.잔여액)} ≠ 배정액 − 누계 {won(doc.예산배정액 - doc.신청누계액)}",
                        문서번호=no))
    out += check_doc_context(doc, ledger, today)
    out += check_doc_numbers([doc], ledger)

    if doc.사업ID in ledger.plan and doc.금번신청액:
        limit = ledger.business_limit(doc.사업ID)
        if doc.금번신청액 > limit:
            out.append(_warn("business_limit", "미확인",
                             f"{doc.사업ID} {ledger.plan[doc.사업ID].사업명} 한도 초과: 신청 {won(doc.금번신청액)} > 남은 한도 {won(limit)}",
                             문서번호=no))
    if doc.금번신청액 and doc.금번신청액 > ledger.remaining():
        out.append(_err("annual_budget", "계산 불일치",
                        f"연간 예산 초과: 신청 {won(doc.금번신청액)} > 잔여 {won(ledger.remaining())}",
                        문서번호=no))
    return out


def validate_report(doc: ReportDoc, ledger, request=None, today: date | None = None) -> list[Finding]:
    out = check_line_items(doc)
    no = [doc.문서번호] if doc.문서번호 else []
    detail = doc.세부합()
    if doc.합계B is None:
        out.append(_err("missing_total", "기재 오류", "합계(B)란 공란", 문서번호=no))
    elif doc.세부내역 and detail != doc.합계B:
        out.append(_err("detail_sum", "계산 불일치",
                        f"세부내역 합 {won(detail)} ≠ 합계(B) {won(doc.합계B)}", 문서번호=no))
    if doc.잔액 is None:
        out.append(_err("missing_balance", "기재 오류", "잔액란 공란", 문서번호=no))
    out += _report_amounts(doc.본당보조, doc.자체예산, doc.합계B, doc.잔액, doc.예산A, no)
    if request is not None and request.금번신청액 != doc.본당보조:
        out.append(_err("subsidy_vs_request", "계산 불일치",
                        f"본당보조 {won(doc.본당보조)} ≠ 연결 신청액 {won(request.금번신청액)}", 문서번호=no))
    out += check_doc_context(doc, ledger, today)
    out += check_doc_numbers([doc], ledger)
    return out


def _report_amounts(subsidy, own, total, balance, budget_a, no, seq=()) -> list[Finding]:
    """예산A = 본당 + 자체, 잔액 = A − 합계B. 반납이 있으면 자체는 0 (잔액 0이면 본당 + 자체 = 합계B)."""
    out = []
    kw = {"문서번호": no, "순번": list(seq)}
    a = (subsidy or 0) + (own or 0)
    if budget_a is not None and budget_a != a:
        out.append(_err("budget_a", "계산 불일치",
                        f"예산(A) {won(budget_a)} ≠ 본당 {won(subsidy)} + 자체 {won(own or 0)}", **kw))
    if total is not None and balance is not None:
        if a - total != balance:
            out.append(_err("report_sum", "계산 불일치",
                            f"본당 {won(subsidy)} + 자체 {won(own or 0)} − 합계(B) {won(total)} ≠ 잔액 {won(balance)}",
                            **kw))
        if balance > 0 and (own or 0) > 0:
            out.append(_err("own_with_balance", "계산 불일치", "반납 잔액이 있는데 자체예산 사용", **kw))
    return out


# ---------------------------------------------------------------- 장부 전체 검사

def _row_findings(r: LedgerRow, ledger) -> list[Finding]:
    s = ledger.settings
    seq, no = [r.순번], [r.문서번호]
    out = []
    if "미기재" in r.문서번호 or not r.문서번호:
        out.append(_err("missing_doc_no", "기재 오류", "문서번호 미기재", 순번=seq, 문서번호=no))
    d = parse_date(r.작성일)
    if d is None:
        out.append(_warn("missing_date", "기재 오류", "작성일 미기재", 순번=seq, 문서번호=no))
    elif d.year != s.기본정보.회계연도:
        out.append(_warn("date_year", "기재 오류", f"작성일 {d.year}년으로 기재", 순번=seq, 문서번호=no))
    named = months_in_name(r.행사명)
    if named and r.월번호 not in named:
        out.append(_warn("name_month", "기재 오류",
                         f"행사명 '{r.행사명}'의 월이 대상 월({r.월})과 다름", 순번=seq, 문서번호=no))
    if r.신청서:
        if r.합계 is None:
            out.append(_err("missing_total", "기재 오류", "합계란 공란", 순번=seq, 문서번호=no))
        elif (r.자체예산 or 0) + (r.본당보조 or 0) != r.합계:
            out.append(_err("income_sum", "계산 불일치",
                            f"자체 + 본당 {won((r.자체예산 or 0) + (r.본당보조 or 0))} ≠ 합계 {won(r.합계)}",
                            순번=seq, 문서번호=no))
        if r.금번신청액 != r.본당보조:
            out.append(_err("request_vs_subsidy", "계산 불일치",
                            f"금번신청액 {won(r.금번신청액)} ≠ 본당보조 {won(r.본당보조)}", 순번=seq, 문서번호=no))
        if r.예산배정액 != s.금액문서기준.예산배정액:
            out.append(_err("allocation", "기재 오류", f"예산배정액 {won(r.예산배정액)}", 순번=seq, 문서번호=no))
        if None not in (r.예산배정액, r.신청누계액, r.잔여액) and r.예산배정액 - r.신청누계액 != r.잔여액:
            out.append(_err("remaining", "계산 불일치",
                            f"잔여액 {won(r.잔여액)} ≠ 배정액 − 누계 {won(r.예산배정액 - r.신청누계액)}",
                            순번=seq, 문서번호=no))
    else:
        if r.본당보조 and r.잔액 is None:
            out.append(_err("missing_balance", "기재 오류", "잔액란 공란", 순번=seq, 문서번호=no))
        out += _report_amounts(r.본당보조, r.자체예산, r.합계, r.잔액, None, no, seq)
        req = ledger.find_ref(r.연결문서번호)
        if req is not None and req.금번신청액 != r.본당보조:
            out.append(_err("subsidy_vs_request", "계산 불일치",
                            f"본당보조 {won(r.본당보조)} ≠ 연결 신청액 {won(req.금번신청액)}", 순번=seq, 문서번호=no))
    return out


def _duplicate_numbers(ledger) -> list[Finding]:
    groups = {}
    for r in ledger.rows:
        if (n := doc_seq(r.문서번호)) is not None:
            groups.setdefault(n, []).append(r)
    return [_err("dup_doc_no", "번호 중복", f"문서번호 {n:03d} 중복",
                 순번=[r.순번 for r in rs], 문서번호=[r.문서번호 for r in rs])
            for n, rs in sorted(groups.items()) if len(rs) > 1]


def _cumulative_breaks(ledger) -> list[Finding]:
    """누계 연속성: 재계산 누계와의 차이가 직전 신청서와 달라지는 곳(오류가 처음 생긴 문서)."""
    out, prev = [], 0
    for step in ledger.cumulative_trace():
        delta = step.차이 - prev
        if delta:
            r = ledger.row(step.순번)
            kind = "누계 누락" if delta < 0 else "계산 불일치"
            out.append(_err("cumulative", kind,
                            f"신청누계액 {won(step.기재)}, 재계산 {won(step.계산)} (이 문서에서 차이 {delta:+,}원)",
                            순번=[r.순번], 문서번호=[r.문서번호]))
        prev = step.차이
    return out


def _unreported(ledger, today: date) -> list[Finding]:
    """지난 달까지의 신청서 중 보고서가 없는 것."""
    return [_warn("no_report", "문서 누락", "집행보고서 없음", 순번=[r.순번], 문서번호=[r.문서번호])
            for r in ledger.requests()
            if not r.연결문서번호 and not r.취소 and r.월번호 < today.month]


def _business_overruns(ledger) -> list[Finding]:
    """사업별 실집행 누계가 계획 본당예산을 처음 넘은 신청서."""
    out = []
    for pid, plan in ledger.plan.items():
        running = 0
        for r in ledger.rows:
            if r.사업ID != pid or r.취소:
                continue
            running += (r.금번신청액 or 0) if r.신청서 else -(r.잔액 or 0)
            if running > plan.본당예산:
                out.append(_warn("business_limit", "미확인",
                                 f"{pid} {plan.사업명} 실집행 누계 {won(running)} > 계획 본당예산 {won(plan.본당예산)}",
                                 순번=[r.순번], 문서번호=[r.문서번호]))
                break
    total = ledger.cumulative_requested()
    if total > ledger.settings.금액문서기준.예산배정액:
        out.append(_err("annual_budget", "계산 불일치",
                        f"연간 신청누계 {won(total)} > 예산배정액 {won(ledger.settings.금액문서기준.예산배정액)}"))
    return out


def merge_findings(findings: list[Finding]) -> list[Finding]:
    """같은 (유형, 대상 순번) 묶음은 하나로 합친다."""
    merged: dict[tuple, Finding] = {}
    for f in findings:
        key = (f.유형, tuple(f.순번))
        if key in merged:
            m = merged[key]
            if f.설명 not in m.설명:
                m.설명 += f" / {f.설명}"
            if f.수준 == "오류":
                m.수준 = "오류"
        else:
            merged[key] = f.model_copy()
    return list(merged.values())


def is_covered(f: Finding, issues: list[Issue]) -> bool:
    """같은 유형이고 대상 순번이 겹치는 이슈가 이미 있으면(상태 무관) 등록된 것으로 본다."""
    return any(i.유형 == f.유형 and set(i.대상순번) & set(f.순번) for i in issues)


def scan_ledger(ledger, issues: list[Issue], today: date | None = None,
                include_registered: bool = False) -> list[Finding]:
    """장부 전체 검사. 기본은 등록된 이슈와 겹치지 않는 새 이상 사항(이슈 후보)만 반환."""
    today = today or date.today()
    found = []
    for r in ledger.rows:
        if not r.취소:
            found += _row_findings(r, ledger)
    found += _duplicate_numbers(ledger)
    found += _cumulative_breaks(ledger)
    found += _unreported(ledger, today)
    found += _business_overruns(ledger)
    found = merge_findings(found)
    return found if include_registered else [f for f in found if not is_covered(f, issues)]
