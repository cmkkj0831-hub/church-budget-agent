"""서식1·2 hwpx → RequestDoc/ReportDoc. fill.py와 같은 칸 매핑(layout)을 쓴다.

세부내역 행 수는 문서마다 다를 수 있으므로(행 복제, 병합된 계정과목 칸) 표에서
'계정과목' 머리 행과 '합계' 행을 찾아 그 사이를 세부내역으로 읽고, 그 아래 칸은 늘어난 행 수만큼 밀어 읽는다.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from core.models import LineItem, ReportDoc, RequestDoc
from hwpx.fill import SUGGESTION_MARK
from hwpx.layout import ITEM_KEYS, LAYOUTS, REPORT, FormLayout, resolve, squash
from hwpx.text import read_cells

REPORT_GUIDE = ("※ 보고 시 잔액과 영수증 등 세법상 적격 지출증빙서 첨부", "※ 차기 계획에 반영할 사항 및 건의사항 등")


@dataclass
class ReadFailure:
    칸: str
    주소: tuple[int, int]
    값: str
    사유: str


@dataclass
class ReadResult:
    kind: str                      # "예산신청서" | "집행보고서"
    doc: RequestDoc | ReportDoc
    failures: list[ReadFailure] = field(default_factory=list)
    item_rows: int = 0             # 표의 세부내역 행 수(기본 7)


class UnknownFormError(ValueError):
    pass


def detect_form(cells) -> FormLayout:
    title = squash(cells.get((0, 0), []))
    for L in LAYOUTS:
        if title == L.title:
            return L
    raise UnknownFormError(f"서식1·2가 아님: 제목 '{title}'")


class _Reader:
    def __init__(self, cells, L: FormLayout):
        self.cells, self.L = cells, L
        self.res = resolve(cells, L)
        self.item_count = self.res.item_count
        self.failures = [ReadFailure(name, (-1, -1), "", "칸을 찾지 못함") for name in self.res.missing]

    def lines(self, name) -> list[str]:
        addr = self.res.cells.get(name)
        return self.cells.get(addr, []) if addr else []

    def text(self, name) -> str:
        return " ".join(t.strip() for t in self.lines(name) if t.strip()).strip()

    def fail(self, name, value, reason):
        self.failures.append(ReadFailure(name, self.res.cells.get(name, (-1, -1)), value, reason))

    def money(self, name, blank=None) -> int | None:
        raw = self.text(name)
        digits = re.sub(r"\(A\)|원|\s", "", raw).replace(",", "")
        if digits == "":
            return blank
        if re.fullmatch(r"-?\d+", digits):
            return int(digits)
        self.fail(name, raw, "금액 아님")
        return blank

    def date_(self, name) -> date | None:
        raw = self.text(name)
        m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", raw)
        if m:
            return date(*map(int, m.groups()))
        if re.search(r"\d", raw):
            self.fail(name, raw, "날짜 형식 아님")
        return None

    def period(self):
        raw = self.text("일시")
        if not re.search(r"\d{4}\s*년\s*\d", raw):
            return None, None, None, None
        t = r"(\d{1,2})(?::(\d{2}))?\s*시(?:\s*(\d{1,2})\s*분)?"
        m = re.search(rf"(\d{{4}})\s*년\s*(\d{{1,2}})\s*월\s*(\d{{1,2}})\s*일\s*{t}\s*부터\s*～\s*"
                      rf"(?:(\d{{1,2}})\s*월\s*(\d{{1,2}})\s*일\s*{t})?.*?까지\s*\(\s*(\d*)\s*박\s*(\d*)\s*일\s*\)", raw)
        if not m:
            self.fail("일시", raw, "일시 형식 아님")
            return None, None, None, None
        g = m.groups()
        y = int(g[0])
        start = datetime(y, int(g[1]), int(g[2]), int(g[3]), int(g[4] or g[5] or 0))
        end = None
        if g[6]:
            ey = y + (1 if int(g[6]) < start.month else 0)
            end = datetime(ey, int(g[6]), int(g[7]), int(g[8]), int(g[9] or g[10] or 0))
        nights = int(g[11]) if g[11] else None
        days = int(g[12]) if g[12] else None
        return start, end, nights, days

    def people(self):
        raw = self.text("인원")
        num = lambda p: int(m.group(1)) if (m := re.search(p, raw)) else None
        return num(r"총\s*(\d+)\s*명"), num(r"남\s*:\s*(\d+)"), num(r"여\s*:\s*(\d+)")

    def body(self) -> list[str]:
        lines = [t.rstrip() for t in self.lines("본문")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def items(self) -> list[LineItem]:
        out, account = [], ""
        cols = self.res.item_cols
        for n in range(self.item_count):
            row = self.res.first_item_row + n
            get = lambda key: " ".join(t.strip() for t in self.cells.get((row, cols[key]), [])).strip() \
                if key in cols else ""
            values = {k: get(k) for k in ITEM_KEYS}
            if values["계정과목"]:
                account = values["계정과목"]
            if not any(values[k] for k in ("내용", "단가", "수량", "금액")):
                continue
            num = lambda s: int(s.replace(",", "")) if re.fullmatch(r"[\d,]+", s) else None
            amount, price, qty = num(values["금액"]), num(values["단가"]), num(values["수량"])
            if amount is None and values["금액"]:
                self.failures.append(ReadFailure(f"세부내역{n + 1}.금액", (row, cols["금액"]),
                                                 values["금액"], "금액 아님"))
            if price is None and values["단가"]:
                self.failures.append(ReadFailure(f"세부내역{n + 1}.단가", (row, cols["단가"]),
                                                 values["단가"], "정수 단가 아님"))
            if price is None:  # 묶인 행('외 N건') 또는 읽기 실패: 금액을 단가로
                price, qty = amount or 0, 1
            out.append(LineItem(계정과목=account, 내용=values["내용"], 단가=price, 수량=qty or 1,
                                금액=amount, 비고=values["비고"]))
        return out


def _sign(s: str) -> str:
    return re.sub(r"\(\s*인\s*\)", "", s).strip()


def _doc_no(s: str) -> str:
    s = squash(s)
    return s if re.search(r"-\d+$", s) else ""


def read_document(path, 사업ID: str = "", 월: int | None = None) -> ReadResult:
    cells = read_cells(path)
    L = detect_form(cells)
    r = _Reader(cells, L)
    start, end, nights, days = r.period()
    total, male, female = r.people()
    issued = r.date_("작성일")
    common = dict(
        사업ID=사업ID, 월=월 or (start.month if start else issued.month if issued else 0),
        작성일=issued, 문서번호=_doc_no(r.text("문서번호")), 행사명=r.text("행사명"),
        주관단체=r.text("주관단체"), 협조단체=r.text("협조단체"),
        기안자=_sign(r.text("기안자")), 기안단체장=_sign(r.text("기안단체장")),
        기안자연락처=r.text("기안자연락처"), 기안단체장연락처=r.text("기안단체장연락처"),
        목적=r.text("목적"), 시작=start, 종료=end, 박=nights, 일=days, 장소=r.text("장소"),
        은행명=r.text("은행명"), 계좌번호=r.text("계좌번호"), 예금주=r.text("예금주"),
        참가대상=r.text("참가대상"), 인원_총=total, 인원_남=male, 인원_여=female,
        세부내역=r.items(),
    )
    if L is REPORT:
        body = [line for line in r.body() if line.strip() not in REPORT_GUIDE]
        suggestion = []
        if SUGGESTION_MARK in body:
            i = body.index(SUGGESTION_MARK)
            body, suggestion = body[:i], body[i + 1:]
            while body and not body[-1].strip():
                body.pop()
        doc = ReportDoc(**common, 경과보고="\n".join(body).strip("\n"), 건의사항="\n".join(suggestion),
                        예산A=r.money("예산A"), 자체예산=r.money("자체예산", blank=0),
                        본당보조=r.money("본당보조"), 합계B=r.money("합계B"), 잔액=r.money("잔액"))
    else:
        doc = RequestDoc(**common, 행사일정="\n".join(r.body()),
                         수입=r.money("수입"), 자체예산=r.money("자체예산", blank=0),
                         본당보조=r.money("본당보조"), 합계=r.money("합계"),
                         예산배정액=r.money("예산배정액"), 금번신청액=r.money("금번신청액"),
                         신청누계액=r.money("신청누계액"), 잔여액=r.money("잔여액"))
    if doc.작성일 is None:
        r.fail("작성일", r.text("작성일"), "작성일 없음")
    if not doc.문서번호:
        r.fail("문서번호", r.text("문서번호"), "문서번호 없음")
    return ReadResult(L.kind, doc, r.failures, r.item_count)


def read_path(path: Path | str, **kw) -> ReadResult:
    return read_document(Path(path), **kw)
