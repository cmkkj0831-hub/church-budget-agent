"""문서 삭제: 앱에서 만든 신청서·보고서를 잘못 만들었을 때 장부에서 뺀다.

- 앱에서 만든 문서(history에 '(행 추가)'가 있는 행)만 지운다. 엑셀에서 옮긴 원본 행은 지우지 않는다
  (원본은 그대로 두고 이슈 처리 — 설명/취소 — 로 다룬다).
- 짝 문서: 신청서를 지우면 연결된 보고서도 함께 지운다. 보고서를 지우면 신청서는 보고 전('진행중')으로 돌아간다.
  같은 반영에서 함께 만든 짝(사후정산)은 함께 지운다.
- 지운 문서만 대상으로 하는 열린 이슈는 '해결-설명됨(문서 삭제)'으로 닫는다. 이슈 자체는 지우지 않는다.
- 기록·되돌리기는 core.changes(record_unit/undo_unit)가 한다 — 지운 행 전체가 history에 남는다.
UI·LLM 의존성 없음.
"""
from dataclasses import dataclass, field
from datetime import date

from core.changes import ROW_ADD, UNDO_PREFIX
from core.issues import IssueBook
from core.ledger import BlockedError, Ledger
from core.models import LedgerRow


@dataclass
class DeletePlan:
    rows: list[LedgerRow] = field(default_factory=list)          # 지울 행(짝 포함)
    unlink: list[tuple[LedgerRow, dict]] = field(default_factory=list)  # 보고 전으로 돌아갈 신청서, {열: 되돌릴 값}
    close_issues: list[str] = field(default_factory=list)        # 함께 닫을 이슈
    warnings: list[str] = field(default_factory=list)

    @property
    def seqs(self) -> list[int]:
        return [r.순번 for r in self.rows]


def _added_units(book: IssueBook) -> dict[int, str]:
    """순번 → 그 행을 추가한 (되돌리지 않은) 반영 이력ID. 나중 반영이 우선."""
    undone = {h.사유[len(UNDO_PREFIX):] for h in book.history if h.사유.startswith(UNDO_PREFIX)}
    out = {}
    for h in book.history:
        if h.대상 == "장부" and h.열 == ROW_ADD and h.순번 is not None and h.이력ID not in undone:
            out[h.순번] = h.이력ID
    return out


def deletable(ledger: Ledger, book: IssueBook) -> list[LedgerRow]:
    """앱에서 만든(지울 수 있는) 문서 행."""
    added = _added_units(book)
    return [r for r in ledger.rows if r.순번 in added]


def plan_delete(ledger: Ledger, book: IssueBook, seqs: list[int]) -> DeletePlan:
    if ledger.read_only:
        raise BlockedError(f"{ledger.year}년은 마감된 연도라 문서를 지울 수 없습니다")
    added = _added_units(book)
    rows = {r.순번: r for r in ledger.rows}
    chosen: dict[int, LedgerRow] = {}
    for s in seqs:
        if s not in rows:
            raise BlockedError(f"순번 {s} 문서가 장부에 없습니다")
        if s not in added:
            raise BlockedError(f"{rows[s].문서번호 or '#' + str(s)}는 원본 장부 문서라 지울 수 없습니다"
                               "(오류 관리에서 이슈로 설명·취소 처리하세요)")
        chosen[s] = rows[s]

    plan = DeletePlan()
    for r in list(chosen.values()):
        pair = ledger.find_ref(r.연결문서번호) if r.연결문서번호 else None
        if pair is None or pair.순번 in chosen:
            continue
        same_unit = added.get(pair.순번) == added.get(r.순번)
        if r.신청서 or same_unit:  # 신청서를 지우면 보고서도, 사후정산 짝은 함께
            if pair.순번 not in added:
                raise BlockedError(f"{r.문서번호}의 짝 {pair.문서번호}는 원본 장부 문서라 함께 지울 수 없습니다")
            chosen[pair.순번] = pair
            plan.warnings.append(f"{r.문서번호}와 짝인 {pair.문서번호}도 함께 지웁니다.")
        else:
            plan.unlink.append((pair, _before_link(book, added.get(r.순번), pair.순번)))
    plan.rows = sorted(chosen.values(), key=lambda r: r.순번)
    gone = set(chosen)

    for i in book.open_issues():
        if i.대상순번 and set(i.대상순번) <= gone:
            plan.close_issues.append(i.이슈ID)
        elif set(i.대상순번) & gone:
            plan.warnings.append(f"{i.이슈ID}는 지우는 문서와 다른 문서를 함께 가리켜 열린 채로 둡니다.")

    # 누계: 지우는 문서 뒤에 이미 만든 신청서는 문서에 적힌 누계가 재계산과 달라진다
    delta = ledger._net(plan.rows)
    first = min(gone)
    later = [r for r in ledger.rows if r.신청서 and not r.취소 and r.순번 > first and r.순번 not in gone]
    if delta and later:
        plan.warnings.append(
            f"뒤에 만든 신청서 {len(later)}건({', '.join(r.문서번호 for r in later[:3])}{' 등' if len(later) > 3 else ''})은 "
            f"문서에 적힌 신청누계가 재계산보다 {delta:,}원 많아집니다. 이미 제출했다면 장부 재검사로 이슈를 남기세요.")

    # 문서번호: 마지막 번호들이면 다음 번호가 돌아가고, 중간이면 빈 번호가 남는다
    after = ledger.copy()
    after.rows = [r for r in after.rows if r.순번 not in gone]
    nxt = after.next_doc_number()
    holes = [r.문서번호 for r in plan.rows if (n := _num(r.문서번호)) is not None and n < nxt]
    if holes:
        plan.warnings.append(f"문서번호 {', '.join(holes)}는 빈 번호로 남습니다(뒤 번호가 이미 쓰였음).")
    return plan


def _before_link(book: IssueBook, unit: str | None, req_seq: int) -> dict:
    """보고서를 만든 반영에서 신청서의 연결문서번호·상태가 바뀌기 전 값(없으면 보고 전 기본값)."""
    out = {"연결문서번호": "", "상태": "진행중"}
    for h in book.history:
        if h.이력ID == unit and h.대상 == "장부" and h.순번 == req_seq and h.열 in out:
            out[h.열] = h.변경전
    return out


def _num(no: str) -> int | None:
    from core.ledger import doc_seq
    return doc_seq(no)


def apply_delete(ledger: Ledger, book: IssueBook, plan: DeletePlan, 사유: str, 처리자: str, today: date) -> None:
    """계획대로 장부에서 행을 빼고 짝 신청서·이슈를 정리한다(저장·이력 기록은 호출하는 쪽)."""
    if not 사유.strip():
        raise BlockedError("삭제 사유를 적어 주세요")
    gone = set(plan.seqs)
    names = ", ".join(r.문서번호 for r in plan.rows)
    ledger.rows = [r for r in ledger.rows if r.순번 not in gone]
    for req, values in plan.unlink:
        for col, val in values.items():
            setattr(req, col, val)
    for iid in plan.close_issues:
        i = book.get(iid)
        i.상태, i.해결방식, i.처리자, i.처리일 = "해결-설명됨", "설명", 처리자, today
        i.해결내용 = f"문서 삭제({names}): {사유.strip()}"
