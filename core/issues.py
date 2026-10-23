"""이슈 관리와 해결. 장부 정정은 apply_resolution으로만 한다. 이력(data/{연도}/history.csv)은
core.changes.record_unit이 반영 단위로 남긴다(되돌리기 단위)."""
from dataclasses import dataclass, field
from pathlib import Path

from core.ledger import (
    MULTI_SEP, BlockedError, BuildResult, CumulativeSummary, Ledger, load_settings, read_csv, write_csv,
)
from core.models import HistoryEntry, Issue, LedgerRow, Resolution
from core.paths import DATA_DIR, year_dir

ISSUE_COLUMNS = list(Issue.model_fields)
HISTORY_COLUMNS = list(HistoryEntry.model_fields)
LIST_FIELDS = ("대상순번", "대상문서번호", "관련이슈", "검증코드")
RESOLVED_STATE = {"정정": "해결-정정", "설명": "해결-설명됨"}
# 화면·엑셀 표시 색: 문서 오류는 빨강, 예산 경고는 주황(문서 자체의 오류가 아님)
DOC_ERROR_COLOR = "C00000"
ISSUE_TYPE_COLORS = {"예산 경고": "C65911", "증빙 누락": "7F7F7F", "미확인": "7F7F7F"}  # 그 밖(문서 오류)은 빨강
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


def _history_from_csv(r: dict) -> HistoryEntry:
    d = dict(r)
    d["순번"] = int(d["순번"]) if d.get("순번") else None
    d.setdefault("대상", "장부")
    d["대상"] = d["대상"] or "장부"
    return HistoryEntry(**d)


class IssueBook:
    """회계연도 하나의 이슈·이력(data/{연도}/issues.csv, history.csv)."""

    def __init__(self, issues: list[Issue], history: list[HistoryEntry],
                 data_dir: Path | None = None, year: int | None = None):
        self.issues = issues
        self.history = history
        self.data_dir = Path(data_dir) if data_dir else None
        self.year = year

    @classmethod
    def load(cls, data_dir=DATA_DIR, year: int | None = None) -> "IssueBook":
        d = Path(data_dir)
        year = year or load_settings(d).현재연도
        yd = year_dir(year, d)
        issues = [issue_from_csv(r) for r in read_csv(yd / "issues.csv")]
        history = [_history_from_csv(r) for r in read_csv(yd / "history.csv")]
        return cls(issues, history, d, year)

    def save(self, data_dir=None) -> None:
        yd = year_dir(self.year, Path(data_dir or self.data_dir))
        yd.mkdir(parents=True, exist_ok=True)
        write_csv(yd / "issues.csv", ISSUE_COLUMNS, [issue_to_csv(i) for i in self.issues])
        write_csv(yd / "history.csv", HISTORY_COLUMNS,
                  [{**h.model_dump(), "순번": "" if h.순번 is None else h.순번} for h in self.history])

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

    def next_history_id(self) -> str:
        n = max((int(h.이력ID.split("-")[1]) for h in self.history if h.이력ID.startswith("H-")), default=0)
        return f"H-{n + 1:04d}"


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


def _row_discrepancy(ledger: Ledger, r: LedgerRow, written_vs_calc: dict[int, int]) -> int:
    """행 안의 금액 어긋남 중 가장 큰 것."""
    from core.ledger import report_amounts
    gaps = []
    if r.신청서:
        if r.합계 is not None:
            gaps.append(abs(r.합계 - (r.본당보조 or 0) - (r.자체예산 or 0)))
        if None not in (r.예산배정액, r.신청누계액, r.잔여액):
            gaps.append(abs(r.잔여액 - (r.예산배정액 - r.신청누계액)))
        gaps.append(abs(written_vs_calc.get(r.순번, 0)))
    elif r.합계 is not None:
        gaps.append(abs((r.본당보조 or 0) + (r.자체예산 or 0) - r.합계))
        req = ledger.find_ref(r.연결문서번호)
        if req is not None and r.잔액 is not None and r.양식 == "신":
            gaps.append(abs(r.잔액 - report_amounts(req.금번신청액 or 0, r.합계)[4]))
    return max(gaps, default=0)


def budget_impact(ledger: Ledger, book: IssueBook, issue: Issue, breaks: dict | None = None,
                  trace: dict | None = None) -> int:
    """이슈의 예산 영향 금액(원, 화면 표시용).
    누계 누락·문서 누락: 그 문서의 누계 단절 / 계산 불일치: 설명의 'N원 차이'·'A원 ≠ B원', 없으면 행 안의 가장 큰 어긋남 /
    예산 경고: 계획 초과액 / 증빙 누락: 설명의 'N원 부족' / 그 밖(기재 오류·번호 중복·미확인): 0."""
    import re
    breaks = ledger.cumulative_breaks() if breaks is None else breaks
    trace = {t.순번: t.차이 for t in ledger.cumulative_trace()} if trace is None else trace
    rows = [r for r in ledger.rows if r.순번 in issue.대상순번]
    if issue.유형 in ("누계 누락", "문서 누락"):
        hit = [abs(breaks[s]) for s in issue.대상순번 if s in breaks]
        return sum(hit) if hit else sum(r.금번신청액 or 0 for r in rows if r.신청서)
    if issue.유형 == "계산 불일치":  # 설명에 적힌 차이가 있으면 그것(같은 행의 다른 이슈 금액과 섞이지 않게)
        m = re.search(r"([\d,]+)원\s*차이", issue.설명)
        if m:
            return int(m.group(1).replace(",", ""))
        m = re.search(r"([\d,]+)원\s*≠[^\d]*([\d,]+)원", issue.설명)
        if m:
            a, b = (int(x.replace(",", "")) for x in m.groups())
            return abs(a - b)
        return max((_row_discrepancy(ledger, r, trace) for r in rows), default=0)
    if issue.유형 == "예산 경고":
        pids = {r.사업ID for r in rows if r.사업ID in ledger.plan}
        return sum(max(0, ledger.business_spent(p) - ledger.plan[p].본당예산) for p in pids)
    if issue.유형 == "증빙 누락":
        m = re.search(r"([\d,]+)원\s*부족", issue.설명)
        return int(m.group(1).replace(",", "")) if m else sum(r.합계 or 0 for r in rows)
    return 0


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

    work = ledger.copy()
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

    # 검증: 연간 잔여가 음수가 되면 거부, 바뀐 행의 계산 오류는 거부
    if work.remaining() < 0:
        raise BlockedError(f"반영 후 잔여액 {work.remaining():,}원 < 0")
    touched = {d["순번"] for d in diff}
    blocking = [f for r in added for f in validate._row_findings(r, work) if f.수준 == "오류"]
    if blocking:
        raise BlockedError("추가 행에 계산 오류", blocking)
    remaining_issues = [i for i in book.issues if i.이슈ID not in {issue.이슈ID, *res.함께해결}]
    # 바뀐 행의 남은 이상 + 이번 반영으로 새로 생기는 누계 단절(예: 취소한 신청서 뒤에 이미 나간 문서의 누계)
    new_breaks = set(work.cumulative_breaks()) - set(ledger.cumulative_breaks())
    leftover = [f for f in validate.scan_ledger(work, remaining_issues, include_registered=False)
                if touched & set(f.순번) or (f.코드 == "cumulative" and set(f.순번) & new_breaks)]

    # 실제 반영
    ledger.rows, ledger.settings = work.rows, work.settings
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
