"""장부 엔진: 입출력, 누계·한도 계산, 신청서·보고서 생성, 장부 반영.

금액·누계·번호는 모두 여기서 계산한다. UI·LLM 의존성 없음.
"""
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from core.models import (
    LedgerRow, PlanItem, ReportDoc, RequestDoc, Settings,
)
from core.paths import DATA_DIR

LEDGER_COLUMNS = list(LedgerRow.model_fields)
LEDGER_INT_COLUMNS = [c for c, f in LedgerRow.model_fields.items() if f.annotation == int | None]
MULTI_SEP = ";"  # 한 칸에 여러 값(대상순번, 관련이슈 등)


# ---------------------------------------------------------------- CSV 도구

def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})


def parse_date(s) -> date | None:
    """'2026년 07월 04일' 같은 원본 표기를 date로. 해석 불가하면 None."""
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", str(s or ""))
    return date(*map(int, m.groups())) if m else None


def format_date(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일"


def doc_seq(no: str) -> int | None:
    """'(예)2026-052' → 52. 미기재는 None."""
    m = re.search(r"\d{4}\s*-\s*(\d+)\s*$", no or "")
    return int(m.group(1)) if m else None


def months_in_name(name: str) -> set[int]:
    """행사명에 적힌 'N월'들. '2026년' 같은 연도는 제외."""
    return {int(m) for m in re.findall(r"(?<!\d)(\d{1,2})\s*월", name or "") if 1 <= int(m) <= 12}


# ---------------------------------------------------------------- 결과 타입

class BlockedError(Exception):
    """오류가 있거나 확인되지 않은 경고가 있어 장부 반영을 막음."""

    def __init__(self, message, findings=()):
        super().__init__(message)
        self.findings = list(findings)


@dataclass
class BuildResult:
    docs: list = field(default_factory=list)       # RequestDoc / ReportDoc
    findings: list = field(default_factory=list)   # validate.Finding

    @property
    def errors(self):
        return [f for f in self.findings if f.수준 == "오류"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.수준 == "경고"]


@dataclass
class CumulativeStep:
    순번: int
    기재: int | None
    계산: int

    @property
    def 차이(self) -> int:
        return (self.기재 or 0) - self.계산


# ---------------------------------------------------------------- 장부

class Ledger:
    def __init__(self, rows: list[LedgerRow], settings: Settings, plan: list[PlanItem],
                 data_dir: Path | None = None):
        self.rows = rows
        self.settings = settings
        self.plan = {p.사업ID: p for p in plan}
        self.data_dir = Path(data_dir) if data_dir else None

    # ---- 입출력
    @classmethod
    def load(cls, data_dir=DATA_DIR) -> "Ledger":
        d = Path(data_dir)
        rows = [LedgerRow(**{k: (None if k in LEDGER_INT_COLUMNS and v == "" else v)
                             for k, v in r.items()})
                for r in read_csv(d / "ledger.csv")]
        settings = Settings(**json.loads((d / "settings.json").read_text(encoding="utf-8")))
        plan = []
        for p in read_csv(d / "plan.csv"):
            plan.append(PlanItem(
                사업ID=p["사업ID"], 시기=p["시기"], 계속신규=p["계속신규"], 사업명=p["사업명"],
                세부내역=p["세부내역"], 자체예산=int(p["자체예산"]), 본당예산=int(p["본당예산"]),
                전년도집행액=int(p["전년도집행액"]) if p["전년도집행액"] else None,
                월별=[int(p[f"{m}월"]) for m in range(1, 13)],
            ))
        return cls(rows, settings, plan, d)

    def save(self, data_dir=None) -> None:
        d = Path(data_dir or self.data_dir)
        write_csv(d / "ledger.csv", LEDGER_COLUMNS, [r.model_dump() for r in self.rows])
        (d / "settings.json").write_text(
            json.dumps(self.settings.model_dump(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    # ---- 조회
    def row(self, seq: int) -> LedgerRow:
        return next(r for r in self.rows if r.순번 == seq)

    def find_ref(self, ref: str) -> LedgerRow | None:
        return next((r for r in self.rows if ref and r.참조 == ref), None)

    def requests(self):
        return [r for r in self.rows if r.신청서]

    def reports(self):
        return [r for r in self.rows if not r.신청서]

    # ---- 문서번호
    def next_doc_number(self) -> int:
        used = [n for r in self.rows if (n := doc_seq(r.문서번호)) is not None]
        return max([self.settings.금액문서기준.다음문서번호, *(n + 1 for n in used)])

    def format_doc_number(self, n: int, kind: str) -> str:
        rules = self.settings.금액문서기준
        prefix = rules.신청서번호접두 if kind == "예산신청서" else rules.보고서번호접두
        return f"{prefix}{n:03d}"

    # ---- 누계·한도 (실집행 기준: 신청액 합 − 보고서 반납액 합, 취소 제외)
    def _net(self, rows) -> int:
        total = 0
        for r in rows:
            if r.취소:
                continue
            total += (r.금번신청액 or 0) if r.신청서 else -(r.잔액 or 0)
        return total

    def cumulative_requested(self, before_seq: int | None = None) -> int:
        return self._net(r for r in self.rows if before_seq is None or r.순번 < before_seq)

    def remaining(self, before_seq: int | None = None) -> int:
        return self.settings.금액문서기준.예산배정액 - self.cumulative_requested(before_seq)

    def business_spent(self, pid: str) -> int:
        return self._net(r for r in self.rows if r.사업ID == pid)

    def business_limit(self, pid: str) -> int:
        return self.plan[pid].본당예산 - self.business_spent(pid)

    def cumulative_trace(self) -> list[CumulativeStep]:
        """신청서마다 문서에 적힌 누계와 처음부터 재계산한 누계."""
        return [CumulativeStep(r.순번, r.신청누계액, self.cumulative_requested(r.순번 + 1))
                for r in self.rows if r.신청서 and not r.취소]

    # ---- 생성
    def _fill_common(self, doc, kind: str, number: int | None = None):
        s = self.settings
        doc.작성일 = doc.작성일 or date.today()
        doc.주관단체 = doc.주관단체 or s.기본정보.주관단체
        doc.기안자 = doc.기안자 or s.기안정보.기안자
        doc.기안단체장 = doc.기안단체장 or s.기안정보.기안단체장
        doc.장소 = doc.장소 or s.기본정보.기본장소
        doc.참가대상 = doc.참가대상 or s.기본정보.참가대상
        if not doc.문서번호:
            doc.문서번호 = self.format_doc_number(number or self.next_doc_number(), kind)
        items = []
        for i in doc.세부내역:
            items.append(i.model_copy(update={"금액": i.단가 * i.수량}) if i.금액 is None else i)
        doc.세부내역 = items

    def build_request(self, draft: RequestDoc, number: int | None = None,
                      today: date | None = None) -> BuildResult:
        from core import validate
        doc = draft.model_copy(deep=True)
        self._fill_common(doc, "예산신청서", number)
        doc.합계 = doc.세부합()
        doc.자체예산 = doc.자체예산 or 0
        doc.본당보조 = doc.합계 - doc.자체예산
        doc.수입 = doc.합계
        doc.금번신청액 = doc.본당보조
        doc.예산배정액 = self.settings.금액문서기준.예산배정액
        doc.신청누계액 = self.cumulative_requested() + doc.금번신청액
        doc.잔여액 = doc.예산배정액 - doc.신청누계액
        return BuildResult([doc], validate.validate_request(doc, self, today=today))

    def build_report(self, draft: ReportDoc, request: LedgerRow | RequestDoc | None,
                     number: int | None = None, today: date | None = None) -> BuildResult:
        """request=None이면 본당보조 없는 자체집행 보고서."""
        from core import validate
        doc = draft.model_copy(deep=True)
        self._fill_common(doc, "집행보고서", number)
        used = doc.세부합()
        applied = (request.금번신청액 or 0) if request is not None else 0
        doc.합계B = used
        doc.본당보조 = applied
        if used >= applied:          # 초과분은 자체예산
            doc.자체예산 = used - applied
            doc.잔액 = 0
        else:                         # 미사용분은 반납
            doc.자체예산 = 0
            doc.잔액 = applied - used
        doc.예산A = doc.본당보조 + doc.자체예산
        if request is not None:
            doc.연결문서번호 = request.참조 if isinstance(request, LedgerRow) else request.문서번호
        return BuildResult([doc], validate.validate_report(doc, self, request, today=today))

    def build_post_settlement(self, report_draft: ReportDoc,
                              today: date | None = None) -> BuildResult:
        """사후정산: 보고서 확정 → 신청서 역생성. 신청액 = min(실사용액, 사업 한도)."""
        used = report_draft.model_copy(update={"세부내역": [
            i if i.금액 is not None else i.model_copy(update={"금액": i.단가 * i.수량})
            for i in report_draft.세부내역]}).세부합()
        limit = self.business_limit(report_draft.사업ID)
        if limit <= 0:
            raise BlockedError(f"{report_draft.사업ID} 사업 한도 소진(남은 한도 {limit:,}원)")
        apply = min(used, limit)
        n = self.next_doc_number()
        shared = report_draft.model_dump(exclude={"경과보고", "예산A", "합계B", "잔액", "문서번호",
                                                  "자체예산", "본당보조", "연결문서번호"})
        req_draft = RequestDoc(**shared, 행사일정=report_draft.경과보고,
                               자체예산=used - apply)
        req_draft.작성일 = report_draft.작성일 or date.today()
        req = self.build_request(req_draft, number=n, today=today)
        req_doc = req.docs[0]
        rep_draft = report_draft.model_copy(update={"작성일": req_doc.작성일})
        rep = self.build_report(rep_draft, req_doc, number=n + 1, today=today)
        rep_doc = rep.docs[0]
        req_doc.연결문서번호 = rep_doc.문서번호
        # 역생성 신청서는 한도까지만이므로 사업 한도 경고는 중복
        findings = [f for f in req.findings + rep.findings if f.코드 != "business_limit"]
        return BuildResult([req_doc, rep_doc], findings)

    # ---- 장부 반영
    def file_name(self, month: int, name: str, kind: str) -> str:
        seq = sum(1 for r in self.rows if r.월번호 == month) + 1
        safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
        return f"{seq}_{safe}_{'신청서' if kind == '예산신청서' else '보고서'}.hwpx"

    def to_row(self, doc) -> LedgerRow:
        kind = "예산신청서" if isinstance(doc, RequestDoc) else "집행보고서"
        row = LedgerRow(
            월=f"{doc.월}월", 구분=kind, 작성일=format_date(doc.작성일),
            자체예산=doc.자체예산, 본당보조=doc.본당보조, 문서번호=doc.문서번호,
            행사명=doc.행사명, 파일명=self.file_name(doc.월, doc.행사명, kind),
            사업ID=doc.사업ID, 연결문서번호=doc.연결문서번호,
        )
        if kind == "예산신청서":
            row.예산배정액, row.금번신청액 = doc.예산배정액, doc.금번신청액
            row.신청누계액, row.잔여액, row.합계 = doc.신청누계액, doc.잔여액, doc.합계
            row.상태 = "진행중"
        else:
            row.합계, row.잔액 = doc.합계B, doc.잔액
            row.상태 = "정산완료" if doc.연결문서번호 else "자체집행"
        return row

    def link(self, report: LedgerRow) -> list[tuple[LedgerRow, str, object, object]]:
        """보고서 행의 연결 신청서에 역연결·상태를 반영. (행, 열, 전, 후) 목록 반환."""
        req = self.find_ref(report.연결문서번호)
        if req is None:
            return []
        state = "사후정산" if parse_date(req.작성일) == parse_date(report.작성일) else "정산완료"
        report.상태 = state
        changes = []
        for col, val in (("연결문서번호", report.참조), ("상태", state)):
            if getattr(req, col) != val:
                changes.append((req, col, getattr(req, col), val))
                setattr(req, col, val)
        return changes

    def commit(self, build: BuildResult, accept_warnings: bool = False) -> list[LedgerRow]:
        """생성 결과를 장부에 추가하고 다음문서번호를 갱신한다(저장은 save)."""
        from core import validate
        if build.errors:
            raise BlockedError("오류가 있어 생성할 수 없음", build.errors)
        if build.warnings and not accept_warnings:
            raise BlockedError("경고 확인 필요", build.warnings)
        dup = validate.check_doc_numbers(build.docs, self)
        if dup:
            raise BlockedError("문서번호 중복", dup)

        added = []
        for doc in build.docs:
            row = self.to_row(doc)  # 앞서 추가한 행도 self.rows에 있으므로 월내순번이 이어진다
            row.순번 = max((r.순번 for r in self.rows), default=0) + 1
            self.rows.append(row)
            added.append(row)
            if not row.신청서 and row.연결문서번호:
                self.link(row)
        self.settings.금액문서기준.다음문서번호 = self.next_doc_number()
        return added
