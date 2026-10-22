"""검증. 오류는 생성을 막고, 경고는 표시한 뒤 사용자가 진행하면 이슈가 된다."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from core.ledger import doc_seq, months_in_name, parse_date, report_amounts
from core.models import MAX_LINE_ITEMS, Issue, IssueType, LedgerRow, ReportDoc, RequestDoc


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

def check_line_items(doc, row_mode: str = "clone") -> list[Finding]:
    out = []
    accounts = {i.계정과목 for i in doc.세부내역}
    if row_mode == "group" and len(doc.세부내역) > MAX_LINE_ITEMS and len(accounts) > MAX_LINE_ITEMS:
        out.append(_err("too_many_items", "기재 오류",
                        f"계정과목 {len(accounts)}종 > 양식 칸 {MAX_LINE_ITEMS}행: 묶기(group)로 맞출 수 없음"))
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
    out = check_line_items(doc, ledger.settings.hwpx_row_mode)
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
        plan = ledger.plan[doc.사업ID]
        limit = ledger.business_limit(doc.사업ID)
        if doc.금번신청액 > limit:
            out.append(_warn("business_limit", "예산 경고",
                             f"{doc.사업ID} {plan.사업명} 계획 초과: 신청 {won(doc.금번신청액)} > 남은 한도 {won(limit)}",
                             문서번호=no))
        projected = ledger.year_end_projection(doc.사업ID, today, doc.금번신청액, doc.월)
        if projected > plan.본당예산:
            out.append(_warn("year_end", "예산 경고",
                             f"{doc.사업ID} {plan.사업명} 연말 예상 {won(projected)} > 계획 {won(plan.본당예산)}",
                             문서번호=no))
    if doc.금번신청액 and doc.금번신청액 > ledger.remaining():
        out.append(_err("annual_budget", "계산 불일치",
                        f"연간 예산 초과: 신청 {won(doc.금번신청액)} > 잔여 {won(ledger.remaining())}",
                        문서번호=no))
    budget = [f for f in out if f.유형 == "예산 경고"]
    return [f for f in out if f.유형 != "예산 경고"] + merge_findings(budget)


def validate_report(doc: ReportDoc, ledger, request=None, today: date | None = None) -> list[Finding]:
    out = check_line_items(doc, ledger.settings.hwpx_row_mode)
    no = [doc.문서번호] if doc.문서번호 else []
    detail = doc.세부합()
    if doc.합계B is None:
        out.append(_err("missing_total", "기재 오류", "합계(B)란 공란", 문서번호=no))
    elif doc.세부내역 and detail != doc.합계B:
        out.append(_err("detail_sum", "계산 불일치",
                        f"세부내역 합 {won(detail)} ≠ 합계(B) {won(doc.합계B)}", 문서번호=no))
    if doc.잔액 is None:
        out.append(_err("missing_balance", "기재 오류", "잔액란 공란", 문서번호=no))
    applied = (request.금번신청액 or 0) if request is not None else (0 if not doc.본당보조 else None)
    out += _report_amounts(doc.양식, applied, doc.본당보조, doc.자체예산, doc.합계B, doc.잔액, doc.예산A, no)
    out += check_doc_context(doc, ledger, today)
    out += check_doc_numbers([doc], ledger)
    return out


def _report_amounts(form, applied, subsidy, own, total, balance, budget_a, no, seq=()) -> list[Finding]:
    """보고서 금액 규칙(신청액 = 연결 신청서 금번신청액, 모르면 None).
    본당 = min(신청, 실사용), 자체 = max(0, 실사용 − 신청), 합계B = 본당 + 자체,
    예산A = 신청 + 자체, 잔액 = A − B = 신청 − 본당.
    구 양식은 '반납금액' 칸을 잔액으로 쓰고 예산란에 실사용액을 적었으므로 구조·A−B 검증을 하지 않는다."""
    if form == "구":
        return []
    out = []
    kw = {"문서번호": no, "순번": list(seq)}
    subsidy, own = subsidy or 0, own or 0
    if total is not None and subsidy + own != total:
        out.append(_err("report_sum", "계산 불일치",
                        f"본당 {won(subsidy)} + 자체 {won(own)} ≠ 합계(B) {won(total)}", **kw))
    if balance and own:
        out.append(_err("own_with_balance", "계산 불일치", "반납 잔액이 있는데 자체예산 사용", **kw))
    if applied is None or total is None:
        return out
    exp_subsidy, exp_own, _, exp_a, exp_balance = report_amounts(applied, total)
    if subsidy != exp_subsidy:
        out.append(_err("subsidy_vs_request", "계산 불일치",
                        f"본당보조 {won(subsidy)} ≠ min(신청 {won(applied)}, 실사용 {won(total)}) = {won(exp_subsidy)}",
                        **kw))
    if budget_a is not None and budget_a != applied + own:
        out.append(_err("budget_a", "계산 불일치",
                        f"예산(A) {won(budget_a)} ≠ 신청 {won(applied)} + 자체 {won(own)}", **kw))
    if balance is not None and balance != applied + own - total:
        out.append(_err("balance", "계산 불일치",
                        f"잔액 {won(balance)} ≠ A − B {won(applied + own - total)}", **kw))
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
        req = ledger.find_ref(r.연결문서번호)
        applied = (req.금번신청액 or 0) if req is not None else (0 if not r.본당보조 else None)
        out += _report_amounts(r.양식, applied, r.본당보조, r.자체예산, r.합계, r.잔액, None, no, seq)
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
    out = []
    for seq, delta in ledger.cumulative_breaks().items():
        r = ledger.row(seq)
        out.append(_err("cumulative", "누계 누락" if delta < 0 else "계산 불일치",
                        f"신청누계액 {won(r.신청누계액)}, 이 문서에서 재계산과 차이 {delta:+,}원",
                        순번=[r.순번], 문서번호=[r.문서번호]))
    return out


def _unreported(ledger, today: date) -> list[Finding]:
    """지난 달까지의 신청서 중 보고서가 없는 것."""
    return [_warn("no_report", "문서 누락", "집행보고서 없음", 순번=[r.순번], 문서번호=[r.문서번호])
            for r in ledger.requests()
            if not r.연결문서번호 and not r.취소 and r.월번호 < today.month]


def _business_overruns(ledger, today: date) -> list[Finding]:
    """사업별 계획 초과(실집행 누계가 계획 본당예산을 처음 넘은 신청서)와 연말 예상 초과(최근 신청서)."""
    out = []
    for pid, plan in ledger.plan.items():
        running = 0
        reqs = []
        for r in ledger.rows:
            if r.사업ID != pid or r.취소:
                continue
            if r.신청서:
                reqs.append(r)
            running += (r.금번신청액 or 0) if r.신청서 else -(r.잔액 or 0)
            if running > plan.본당예산 and not any(f.코드 == "business_limit" and f.설명.startswith(pid) for f in out):
                out.append(_warn("business_limit", "예산 경고",
                                 f"{pid} {plan.사업명} 실집행 누계 {won(running)} > 계획 본당예산 {won(plan.본당예산)}",
                                 순번=[r.순번], 문서번호=[r.문서번호]))
        projected = ledger.year_end_projection(pid, today)
        if reqs and projected > plan.본당예산:
            last = reqs[-1]
            out.append(_warn("year_end", "예산 경고",
                             f"{pid} {plan.사업명} 연말 예상 {won(projected)} > 계획 {won(plan.본당예산)}",
                             순번=[last.순번], 문서번호=[last.문서번호]))
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
    """대상 순번이 겹치는 이슈가 이미 있고(상태 무관), 유형이 같거나 그 이슈의 검증코드에
    이 검사 코드가 있으면 등록된 것으로 본다(예: 금액 영향 없는 기재 방식 차이를 설명한 이슈)."""
    return any(set(i.대상순번) & set(f.순번) and (i.유형 == f.유형 or f.코드 in i.검증코드)
               for i in issues)


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
    found += _business_overruns(ledger, today)
    if not include_registered:
        found = [f for f in found if not is_covered(f, issues)]
    return merge_findings(found)
