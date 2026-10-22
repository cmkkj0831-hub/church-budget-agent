"""빈 공식 양식(templates/*.hwpx)의 표 칸을 채워 신청서·보고서 hwpx를 만든다.

표 구조는 건드리지 않고, 칸 주소(행, 열)의 문단 텍스트만 바꾼다. 바꾼 문단은 줄 배치 정보
(linesegarray)를 지워 한글이 열 때 다시 배치하게 한다.
"""
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from core.models import MAX_LINE_ITEMS, DocBase, LedgerRow, ReportDoc, RequestDoc
from core.paths import TEMPLATES_DIR
from hwpx.text import extract_text

REQUEST_TEMPLATE = TEMPLATES_DIR / "request_template.hwpx"
REPORT_TEMPLATE = TEMPLATES_DIR / "report_template.hwpx"
SECTION = "Contents/section0.xml"
PREVIEW = "Preview/PrvText.txt"

# 칸 주소 (행, 열) — templates/*.hwpx 표 기준
COMMON = {
    "작성일": (1, 0), "문서번호": (2, 1), "행사명": (3, 1), "주관단체": (5, 1),
    "기안자": (6, 1), "기안단체장": (7, 1), "목적": (8, 1), "일시": (9, 1),
    "장소": (10, 1), "참가대상": (11, 1), "본문": (12, 1),
}
REQUEST_CELLS = COMMON | {
    "협조단체": (5, 15), "기안자연락처": (6, 15), "기안단체장연락처": (7, 15),
    "은행명": (10, 6), "계좌번호": (10, 13), "예금주": (10, 26), "인원": (11, 9),
    "수입": (13, 1), "자체예산": (13, 19), "본당보조": (14, 19), "합계": (23, 18),
    "예산배정액": (24, 1), "금번신청액": (24, 5), "신청누계액": (24, 16), "잔여액": (24, 24),
}
REPORT_CELLS = COMMON | {
    "협조단체": (5, 12), "기안자연락처": (6, 12), "기안단체장연락처": (7, 12),
    "은행명": (10, 4), "계좌번호": (10, 8), "예금주": (10, 19), "인원": (11, 10),
    "예산A": (13, 1), "자체예산": (13, 15), "본당보조": (14, 15), "합계B": (23, 13), "잔액": (24, 13),
}
ITEM_FIRST_ROW = 16
REQUEST_ITEM_COLS = {"계정과목": 0, "내용": 1, "단가": 7, "수량": 12, "금액": 18, "비고": 23}
REPORT_ITEM_COLS = {"계정과목": 0, "내용": 1, "단가": 6, "수량": 8, "금액": 13, "비고": 18}
SIGN_WIDTH = 23  # '(인)' 앞 칸 너비(반각 기준)


# ---------------------------------------------------------------- 서식

def won(n: int) -> str:
    return f"{n:,}원"


def fmt_date(d) -> str:
    return f" {d.year}년 {d.month}월 {d.day}일"


def fmt_doc_no(no: str) -> str:
    """'(예)2026-053' → '(예)2026 - 053' (양식 표기)."""
    return re.sub(r"\s*-\s*", " - ", no, count=1)


def _width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def fmt_sign(name: str) -> str:
    return f"{name}{' ' * max(1, SIGN_WIDTH - _width(name))}(인)"


def _hour(t: datetime) -> str:
    return f"{t.hour}시" + (f" {t.minute}분" if t.minute else "")


def fmt_period(doc: DocBase) -> str:
    s, e = doc.시작, doc.종료
    nights = f"{doc.박:>3}" if doc.박 is not None else "   "
    days = f"{doc.일:>3}" if doc.일 is not None else "   "
    end = f"{e.month}월 {e.day}일 {_hour(e)}" if e else "  월   일   시"
    return f"  {s.year}년 {s.month}월 {s.day}일 {_hour(s)}부터  ～  {end}까지 ({nights}박{days}일)"


def fmt_people(doc: DocBase) -> str:
    n = lambda v: "   " if v is None else str(v)
    return f" 총 {n(doc.인원_총)}명 (남: {n(doc.인원_남)}명 / 여: {n(doc.인원_여)}명)"


# ---------------------------------------------------------------- XML 칸 교체

_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_P = re.compile(r"<hp:p\b[^>]*>.*?</hp:p>", re.S)


def _paragraph(proto: str, text: str) -> str:
    """proto 문단의 속성·글자모양을 유지한 새 문단."""
    open_tag = re.match(r"<hp:p\b[^>]*>", proto).group()
    char = re.search(r'<hp:run charPrIDRef="(\d+)"', proto)
    char_ref = char.group(1) if char else "0"
    body = f"<hp:t>{escape(text)}</hp:t>" if text else ""
    run = f'<hp:run charPrIDRef="{char_ref}">{body}</hp:run>' if body else f'<hp:run charPrIDRef="{char_ref}"/>'
    return f"{open_tag}{run}</hp:p>"


def _replace_cell(cell: str, lines: list[str]) -> str:
    paras = _P.findall(cell)
    start = cell.index(paras[0])
    end = cell.index(paras[-1]) + len(paras[-1])
    new = "".join(_paragraph(paras[0], line) for line in (lines or [""]))
    return cell[:start] + new + cell[end:]


def fill_section(xml: str, values: dict[tuple[int, int], list[str]]) -> str:
    def sub(m):
        cell = m.group()
        a = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', cell)
        key = (int(a.group(2)), int(a.group(1)))
        return _replace_cell(cell, values[key]) if key in values else cell

    out = _TC.sub(sub, xml)
    missing = set(values) - {
        (int(r), int(c)) for c, r in re.findall(r'colAddr="(\d+)" rowAddr="(\d+)"', xml)}
    if missing:
        raise KeyError(f"양식에 없는 칸: {sorted(missing)}")
    return out


# ---------------------------------------------------------------- 문서 → 칸 값

def _items(doc: DocBase, cols: dict[str, int]) -> dict:
    if len(doc.세부내역) > MAX_LINE_ITEMS:
        raise ValueError(f"세부내역 {len(doc.세부내역)}건 > 양식 칸 {MAX_LINE_ITEMS}건")
    same_account = len({i.계정과목 for i in doc.세부내역}) == 1
    out = {}
    for n, item in enumerate(doc.세부내역):
        row = ITEM_FIRST_ROW + n
        amount = item.금액 if item.금액 is not None else item.단가 * item.수량
        account = item.계정과목 if (n == 0 or not same_account) else ""
        for key, text in (("계정과목", account), ("내용", item.내용), ("단가", f"{item.단가:,}"),
                          ("수량", str(item.수량)), ("금액", f"{amount:,}"), ("비고", item.비고)):
            out[(row, cols[key])] = [text]
    return out


def _common(doc: DocBase, cells: dict) -> dict:
    v = {}

    def put(key, text):
        if text:
            v[cells[key]] = text if isinstance(text, list) else [text]

    put("작성일", fmt_date(doc.작성일) if doc.작성일 else "")
    put("문서번호", fmt_doc_no(doc.문서번호) if doc.문서번호 else "")
    put("행사명", doc.행사명)
    put("주관단체", doc.주관단체)
    put("협조단체", doc.협조단체)
    put("기안자", fmt_sign(doc.기안자) if doc.기안자 else "")
    put("기안단체장", fmt_sign(doc.기안단체장) if doc.기안단체장 else "")
    put("기안자연락처", doc.기안자연락처)
    put("기안단체장연락처", doc.기안단체장연락처)
    put("목적", doc.목적)
    put("일시", fmt_period(doc) if doc.시작 else "")
    put("장소", doc.장소)
    put("은행명", doc.은행명)
    put("계좌번호", doc.계좌번호)
    put("예금주", doc.예금주)
    put("참가대상", doc.참가대상)
    if any(x is not None for x in (doc.인원_총, doc.인원_남, doc.인원_여)):
        put("인원", fmt_people(doc))
    return v


def request_values(doc: RequestDoc) -> dict:
    c = REQUEST_CELLS
    v = _common(doc, c)
    if doc.행사일정:
        v[c["본문"]] = doc.행사일정.split("\n")
    for key in ("수입", "본당보조"):
        if getattr(doc, key) is not None:
            v[c[key]] = [won(getattr(doc, key))]
    if doc.자체예산:
        v[c["자체예산"]] = [" " + won(doc.자체예산)]
    if doc.합계 is not None:
        v[c["합계"]] = [won(doc.합계)]
    for key in ("예산배정액", "금번신청액", "신청누계액", "잔여액"):
        if getattr(doc, key) is not None:
            v[c[key]] = [f"{getattr(doc, key):,}"]
    v |= _items(doc, REQUEST_ITEM_COLS)
    return v


def report_values(doc: ReportDoc) -> dict:
    c = REPORT_CELLS
    v = _common(doc, c)
    body = doc.경과보고.split("\n") if doc.경과보고 else []
    if doc.건의사항:
        body += ["", "※ 차기 계획에 반영할 사항 및 건의사항", *doc.건의사항.split("\n")]
    if body:
        v[c["본문"]] = body
    if doc.예산A is not None:
        v[c["예산A"]] = [f"{doc.예산A:,}원(A)"]
    if doc.자체예산:
        v[c["자체예산"]] = [" " + won(doc.자체예산)]
    if doc.본당보조 is not None:
        v[c["본당보조"]] = [won(doc.본당보조)]
    if doc.합계B is not None:
        v[c["합계B"]] = [won(doc.합계B)]
    if doc.잔액 is not None:
        v[c["잔액"]] = [won(doc.잔액)]
    v |= _items(doc, REPORT_ITEM_COLS)
    return v


# ---------------------------------------------------------------- 파일 쓰기

def render(doc: RequestDoc | ReportDoc, path, template=None) -> Path:
    """문서를 hwpx로 저장. 템플릿의 다른 파일은 순서·압축 방식 그대로 복사한다."""
    is_request = isinstance(doc, RequestDoc)
    template = Path(template or (REQUEST_TEMPLATE if is_request else REPORT_TEMPLATE))
    values = request_values(doc) if is_request else report_values(doc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(template) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == SECTION:
                data = fill_section(data.decode("utf-8"), values).encode("utf-8")
            dst.writestr(info, data, compress_type=info.compress_type)
    _write_preview(tmp)
    tmp.replace(path)
    return path


def _write_preview(path: Path) -> None:
    """미리보기 텍스트(Preview/PrvText.txt)를 채운 내용으로 바꾼다."""
    text = extract_text(path)
    tmp = path.with_suffix(".prv")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = text.encode("utf-8") if info.filename == PREVIEW else src.read(info)
            dst.writestr(info, data, compress_type=info.compress_type)
    tmp.replace(path)


def output_path(out_dir, row: LedgerRow) -> Path:
    """out/{월}/{파일명} — 월 폴더는 사업이 속한 월."""
    return Path(out_dir) / row.월 / row.파일명


def render_committed(docs: list, rows: list[LedgerRow], out_dir) -> list[Path]:
    """commit으로 장부에 추가된 행과 같은 순서의 문서를 파일로 만든다."""
    if len(docs) != len(rows):
        raise ValueError("문서와 장부 행 수가 다름")
    return [render(d, output_path(out_dir, r)) for d, r in zip(docs, rows)]
