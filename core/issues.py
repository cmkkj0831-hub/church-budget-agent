"""이슈 관리와 해결. 장부 변경은 apply_resolution으로만 하고 data/history.csv에 이력을 남긴다."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.ledger import (
    MULTI_SEP, BlockedError, BuildResult, CumulativeSummary, Ledger, read_csv, write_csv,
)
from core.models import HistoryEntry, Issue, LedgerRow, Resolution
from core.paths import DATA_DIR

ISSUE_COLUMNS = list(Issue.model_fields)
HISTORY_COLUMNS = list(HistoryEntry.model_fields)
LIST_FIELDS = ("대상순번", "대상문서번호", "관련이슈")
RESOLVED_STATE = {"정정": "해결-정정", "설명": "해결-설명됨"}
# 화면·엑셀 표시 색: 문서 오류는 빨강, 예산 경고는 주황(문서 자체의 오류가 아님)
DOC_ERROR_COLOR = "C00000"
ISSUE_TYPE_COLORS = {"예산 경고": "C65911"}
CUMULATIVE_TYPES = ("누계 누락", "계산 불일치")


def issue_color(issue_type: str) -> str:
    return ISSUE_TYPE_COLORS.get(issue_type, DOC_ERROR_COLOR)


def _split(v: str) -> list[str]:
    return [x for x in (v or "").split(MULTI_SEP) if x.strip()]


def issue_from_csv(d: dict) -> Issue:
    d = dict(d)
    for k in LIST_FIELDS:
        d[k] = _split(d.get(k, ""))
    d["대상순번"] = [int(x) for x in d["대상순번"]]
    d["처리일"] = d.get("처리일") or None
    return Issue(**d)


def issue_to_csv(i: Issue) -> dict:
    d = i.model_dump()
    for k in LIST_FIELDS:
        d[k] = MULTI_SEP.join(map(str, d[k]))
    d["처리일"] = i.처리일.isoformat() if i.처리일 else ""
    return d


class IssueBook:
    def __init__(self, issues: list[Issue], history: list[HistoryEntry],
                 data_dir: Path | None = None):
        self.issues = issues
        self.history = history
        self.data_dir = Path(data_dir) if data_dir else None

    @classmethod
    def load(cls, data_dir=DATA_DIR) -> "IssueBook":
        d = Path(data_dir)
        issues = [issue_from_csv(r) for r in read_csv(d / "issues.csv")]
        history = [HistoryEntry(**r) for r in read_csv(d / "history.csv")]
        return cls(issues, history, d)

    def save(self, data_dir=None) -> None:
        d = Path(data_dir or self.data_dir)
        write_csv(d / "issues.csv", ISSUE_COLUMNS, [issue_to_csv(i) for i in self.issues])
        write_csv(d / "history.csv", HISTORY_COLUMNS, [h.model_dump() for h in self.history])

    def get(self, issue_id: str) -> Issue:
        return next(i for i in self.issues if i.이슈ID == issue_id)

    def open_issues(self, seq: int | None = None) -> list[Issue]:
        return [i for i in self.issues if i.열림 and (seq is None or seq in i.대상순번)]

    def ledger_remarks_detail(self) -> dict[int, list[tuple[str, str]]]:
        """문서별 열린 이슈 (유형, 설명) 목록. 유형별 색 표시용."""
        out: dict[int, list[tuple[str, str]]] = {}
        for i in self.open_issues():
            for seq in i.대상순번:
                out.setdefault(seq, []).append((i.유형, i.설명))
        return out

    def ledger_remarks(self) -> dict[int, str]:
        """비고 = 문서별 열린 이슈 설명. 해결되면 사라진다."""
        return {k: " / ".join(d for _, d in v) for k, v in self.ledger_remarks_detail().items()}

    def next_id(self) -> str:
        n = max((int(i.이슈ID.split("-")[1]) for i in self.issues), default=0)
        return f"I-{n + 1:03d}"

    def _log(self, issue_id, seq, col, before, after, reason, handler, when):
        self.history.append(HistoryEntry(
            이력ID=f"H-{len(self.history) + 1:04d}", 일시=when, 이슈ID=issue_id, 순번=seq, 열=col,
            변경전="" if before is None else str(before), 변경후="" if after is None else str(after),
            사유=reason, 처리자=handler,
        ))


def create_issue(book: IssueBook, finding, rows: list[LedgerRow] = (), 근거: str = "") -> Issue:
    """경고를 무시하고 생성을 진행했을 때 그 경고를 이슈로 남긴다."""
    seqs = list(finding.순번) or [r.순번 for r in rows]
    nos = list(finding.문서번호) or [r.문서번호 for r in rows]
    issue = Issue(이슈ID=book.next_id(), 대상순번=seqs, 대상문서번호=nos, 유형=finding.유형,
                  설명=finding.설명, 근거=근거 or f"생성 시 경고({finding.코드})를 확인하고 진행")
    book.issues.append(issue)
    return issue


def commit_with_issues(ledger: Ledger, book: IssueBook, build: BuildResult,
                       accept_warnings: bool = False) -> tuple[list[LedgerRow], list[Issue]]:
    """장부 반영 + 확인된 경고를 이슈로 등록."""
    rows = ledger.commit(build, accept_warnings=accept_warnings)
    by_no = {r.문서번호: r for r in rows}
    created = []
    for f in build.warnings:
        targets = [by_no[n] for n in f.문서번호 if n in by_no] or rows
        created.append(create_issue(book, f.model_copy(update={"순번": [], "문서번호": []}), targets))
    return rows, created


@dataclass
class CumulativeReconciliation:
    """문서 기준 누계와 재계산 누계의 차이를 열린 이슈로 설명한다."""
    summary: CumulativeSummary
    항목: list[dict] = field(default_factory=list)  # {순번, 문서번호, 차이, 이슈}
    미설명: list[int] = field(default_factory=list)

    @property
    def 설명됨(self) -> bool:
        return not self.미설명


def cumulative_reconciliation(ledger: Ledger, book: IssueBook) -> CumulativeReconciliation:
    summary = ledger.cumulative_summary()
    rec = CumulativeReconciliation(summary)
    for seq, delta in summary.단절.items():
        ids = [i.이슈ID for i in book.open_issues(seq) if i.유형 in CUMULATIVE_TYPES]
        rec.항목.append({"순번": seq, "문서번호": ledger.row(seq).문서번호, "차이": delta, "이슈": ids})
        if not ids:
            rec.미설명.append(seq)
    return rec


@dataclass
class ResolutionResult:
    diff: list[dict] = field(default_factory=list)   # {순번, 열, 변경전, 변경후}
    누계_전: int = 0
    누계_후: int = 0
    잔여_후: int = 0
    경고: list = field(default_factory=list)          # 반영 후 대상 행에 남은 이상(Finding)


def apply_resolution(ledger: Ledger, book: IssueBook, issue: Issue | str,
                     res: Resolution) -> ResolutionResult:
    """이슈 해결을 장부에 반영한다(저장은 ledger.save / book.save).

    정정: 대상 행 값 변경 또는 행 추가. 설명: 값은 두고 사유 기록(상태 열 변경만 허용).
    복사본에 먼저 반영해 누계를 재계산·검증하고, 문제가 없을 때만 실제 장부에 적용한다.
    """
    from core import validate
    issue = book.get(issue) if isinstance(issue, str) else issue
    together = [book.get(i) for i in res.함께해결]
    for i in [issue, *together]:
        if not i.열림:
            raise BlockedError(f"{i.이슈ID}는 이미 {i.상태}")
    if res.방식 == "설명" and (res.추가행 or any(c.열 != "상태" for c in res.변경)):
        raise BlockedError("설명 해결은 상태 열만 바꿀 수 있음")
    if res.방식 == "정정" and not (res.변경 or res.추가행):
        raise BlockedError("정정 해결에는 장부 변경이나 행 추가가 필요함")

    work = Ledger([r.model_copy() for r in ledger.rows], ledger.settings.model_copy(deep=True),
                  list(ledger.plan.values()), ledger.data_dir)
    before_total = ledger.cumulative_requested()
    diff = []
    targets = set(issue.대상순번).union(*(t.대상순번 for t in together))

    for c in res.변경:
        if c.순번 not in targets:
            raise BlockedError(f"순번 {c.순번}는 이슈 대상이 아님")
        if c.열 not in LedgerRow.model_fields or c.열 == "순번":
            raise BlockedError(f"변경할 수 없는 열: {c.열}")
        row = work.row(c.순번)
        old = getattr(row, c.열)
        setattr(row, c.열, c.값)
        LedgerRow.model_validate(row.model_dump())  # 타입 확인
        diff.append({"순번": c.순번, "열": c.열, "변경전": old, "변경후": c.값})

    added = []
    for new in res.추가행:
        row = new.model_copy()
        row.순번 = max(r.순번 for r in work.rows) + 1
        if not row.문서번호:
            row.문서번호 = work.format_doc_number(work.next_doc_number(), row.구분)
        if not row.파일명:
            row.파일명 = work.file_name(row.월번호, row.행사명, row.구분)
        work.rows.append(row)
        added.append(row)
        diff.append({"순번": row.순번, "열": "(행 추가)", "변경전": None,
                     "변경후": f"{row.구분} {row.문서번호} {row.행사명}"})
        if not row.신청서 and row.연결문서번호:
            for req, col, old, val in work.link(row):
                diff.append({"순번": req.순번, "열": col, "변경전": old, "변경후": val})
    work.settings.금액문서기준.다음문서번호 = work.next_doc_number()

    # 검증: 연간 잔여가 음수가 되면 거부, 바뀐 행의 계산 오류는 거부
    if work.remaining() < 0:
        raise BlockedError(f"반영 후 잔여액 {work.remaining():,}원 < 0")
    touched = {d["순번"] for d in diff}
    blocking = [f for r in added for f in validate._row_findings(r, work) if f.수준 == "오류"]
    if blocking:
        raise BlockedError("추가 행에 계산 오류", blocking)
    remaining_issues = [i for i in book.issues if i.이슈ID not in {issue.이슈ID, *res.함께해결}]
    leftover = [f for f in validate.scan_ledger(work, remaining_issues, include_registered=False)
                if touched & set(f.순번)]

    # 실제 반영
    ledger.rows, ledger.settings = work.rows, work.settings
    when = datetime.now().isoformat(timespec="seconds")
    for d in diff:
        book._log(issue.이슈ID, d["순번"], d["열"], d["변경전"], d["변경후"], res.내용, res.처리자, when)
    state = RESOLVED_STATE[res.방식]
    for i in [issue, *together]:
        i.상태 = state
        i.해결방식 = res.방식
        i.해결내용 = res.내용 if i is issue else f"{issue.이슈ID}와 함께 해결: {res.내용}"
        i.처리자 = res.처리자
        i.처리일 = res.처리일
        if res.첨부:
            i.첨부 = res.첨부
    return ResolutionResult(diff, before_total, ledger.cumulative_requested(),
                            ledger.remaining(), leftover)
