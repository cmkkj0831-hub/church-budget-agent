"""빈 공식 양식(templates/*.hwpx)의 표 칸을 채워 신청서·보고서 hwpx를 만든다.

표 구조는 그대로 두고 칸 주소(행, 열)의 문단 텍스트만 바꾼다. 바꾼 문단은 줄 배치 정보
(linesegarray)를 지워 한글이 열 때 다시 배치하게 한다. 세부내역이 7행을 넘으면
row_mode="clone"이면 행을 복제하고, "group"이면 같은 계정과목 항목을 묶어 7행에 맞춘다.
"""
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from core.models import MAX_LINE_ITEMS, DocBase, ReportDoc, RequestDoc, RowMode
from hwpx.layout import ITEM_KEYS, REPORT, REPORT_CELLS, REQUEST, REQUEST_CELLS, Resolved, resolve
from hwpx.table import expand_item_rows
from hwpx.text import cells_from_xml, extract_text

__all__ = ["render", "item_rows", "REQUEST_CELLS", "REPORT_CELLS",
           "REQUEST_TEMPLATE", "REPORT_TEMPLATE"]

REQUEST_TEMPLATE = REQUEST.template
REPORT_TEMPLATE = REPORT.template
SECTION = "Contents/section0.xml"
PREVIEW = "Preview/PrvText.txt"
SIGN_WIDTH = 23  # '(인)' 앞 칸 너비(반각 기준)
SUGGESTION_MARK = "※ 차기 계획에 반영할 사항 및 건의사항"


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


# ---------------------------------------------------------------- 세부내역 행

def _amount(item) -> int:
    return item.금액 if item.금액 is not None else item.단가 * item.수량


def _row(item) -> dict:
    return {"계정과목": item.계정과목, "내용": item.내용, "단가": f"{item.단가:,}",
            "수량": str(item.수량), "금액": f"{_amount(item):,}", "비고": item.비고}


def _grouped(items) -> list[dict]:
    """같은 계정과목의 뒤쪽 항목을 '내용 외 N건'으로 묶어 7행에 맞춘다(금액 합산, 단가·수량 비움).
    줄여야 할 만큼만, 항목이 많은 계정과목부터 묶는다."""
    need = len(items) - MAX_LINE_ITEMS
    groups: dict[str, list[int]] = {}
    for i, item in enumerate(items):
        groups.setdefault(item.계정과목, []).append(i)
    merged: dict[int, list[int]] = {}  # 묶음 첫 항목 → 묶인 항목들
    for idx in sorted(groups.values(), key=len, reverse=True):
        if need <= 0:
            break
        take = min(need, len(idx) - 1)
        if take:
            tail = idx[len(idx) - take - 1:]
            merged[tail[0]] = tail
            need -= take
    if need > 0:
        raise ValueError(f"계정과목 {len(groups)}종이라 {MAX_LINE_ITEMS}행으로 묶을 수 없음")
    skip = {i for tail in merged.values() for i in tail[1:]}
    rows = []
    for i, item in enumerate(items):
        if i in skip:
            continue
        if i in merged:
            tail = merged[i]
            rows.append({"계정과목": item.계정과목, "내용": f"{item.내용} 외 {len(tail) - 1}건",
                         "단가": "", "수량": "", "금액": f"{sum(_amount(items[j]) for j in tail):,}",
                         "비고": item.비고})
        else:
            rows.append(_row(item))
    return rows


def item_rows(doc: DocBase, row_mode: RowMode = "clone") -> list[dict]:
    """양식에 쓸 세부내역 행. 계정과목이 모두 같으면 첫 행에만 쓴다(예시 문서의 병합 칸과 같은 모양)."""
    items = doc.세부내역
    rows = _grouped(items) if row_mode == "group" and len(items) > MAX_LINE_ITEMS else [_row(i) for i in items]
    if len({r["계정과목"] for r in rows}) == 1:
        for r in rows[1:]:
            r["계정과목"] = ""
    return rows


# ---------------------------------------------------------------- XML 칸 교체

_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_P = re.compile(r"<hp:p\b[^>]*>.*?</hp:p>", re.S)


def _paragraph(proto: str, text: str) -> str:
    """proto 문단의 속성·글자모양을 유지한 새 문단."""
    open_tag = re.match(r"<hp:p\b[^>]*>", proto).group()
    char = re.search(r'<hp:run charPrIDRef="(\d+)"', proto)
    char_ref = char.group(1) if char else "0"
    if text:
        return f'{open_tag}<hp:run charPrIDRef="{char_ref}"><hp:t>{escape(text)}</hp:t></hp:run></hp:p>'
    return f'{open_tag}<hp:run charPrIDRef="{char_ref}"/></hp:p>'


def _replace_cell(cell: str, lines: list[str]) -> str:
    paras = _P.findall(cell)
    start = cell.index(paras[0])
    end = cell.index(paras[-1]) + len(paras[-1])
    new = "".join(_paragraph(paras[0], line) for line in (lines or [""]))
    return cell[:start] + new + cell[end:]


def fill_section(xml: str, values: dict[tuple[int, int], list[str]]) -> str:
    present = {(int(r), int(c)) for c, r in re.findall(r'colAddr="(\d+)" rowAddr="(\d+)"', xml)}
    missing = set(values) - present
    if missing:
        raise KeyError(f"양식에 없는 칸: {sorted(missing)}")

    def sub(m):
        cell = m.group()
        a = re.search(r'colAddr="(\d+)" rowAddr="(\d+)"', cell)
        key = (int(a.group(2)), int(a.group(1)))
        return _replace_cell(cell, values[key]) if key in values else cell

    return _TC.sub(sub, xml)


# ---------------------------------------------------------------- 문서 → 필드 값

def _common(doc: DocBase) -> dict[str, list[str]]:
    v = {
        "작성일": fmt_date(doc.작성일) if doc.작성일 else "",
        "문서번호": fmt_doc_no(doc.문서번호) if doc.문서번호 else "",
        "행사명": doc.행사명, "주관단체": doc.주관단체, "협조단체": doc.협조단체,
        "기안자": fmt_sign(doc.기안자) if doc.기안자 else "",
        "기안단체장": fmt_sign(doc.기안단체장) if doc.기안단체장 else "",
        "기안자연락처": doc.기안자연락처, "기안단체장연락처": doc.기안단체장연락처,
        "목적": doc.목적, "일시": fmt_period(doc) if doc.시작 else "", "장소": doc.장소,
        "은행명": doc.은행명, "계좌번호": doc.계좌번호, "예금주": doc.예금주, "참가대상": doc.참가대상,
        "인원": fmt_people(doc) if any(x is not None for x in (doc.인원_총, doc.인원_남, doc.인원_여)) else "",
    }
    return {k: [t] for k, t in v.items() if t}


def request_fields(doc: RequestDoc) -> dict[str, list[str]]:
    v = _common(doc)
    if doc.행사일정:
        v["본문"] = doc.행사일정.split("\n")
    for key in ("수입", "본당보조", "합계"):
        if getattr(doc, key) is not None:
            v[key] = [won(getattr(doc, key))]
    if doc.자체예산:
        v["자체예산"] = [" " + won(doc.자체예산)]
    for key in ("예산배정액", "금번신청액", "신청누계액", "잔여액"):
        if getattr(doc, key) is not None:
            v[key] = [f"{getattr(doc, key):,}"]
    return v


def report_fields(doc: ReportDoc) -> dict[str, list[str]]:
    v = _common(doc)
    body = doc.경과보고.split("\n") if doc.경과보고 else []
    if doc.건의사항:
        body += ["", SUGGESTION_MARK, *doc.건의사항.split("\n")]
    if body:
        v["본문"] = body
    if doc.예산A is not None:
        v["예산A"] = [f"{doc.예산A:,}원(A)"]
    if doc.자체예산:
        v["자체예산"] = [" " + won(doc.자체예산)]
    for key in ("본당보조", "합계B", "잔액"):
        if getattr(doc, key) is not None:
            v[key] = [won(getattr(doc, key))]
    return v


def cell_values(fields: dict[str, list[str]], rows: list[dict], res: Resolved) -> dict:
    """필드 값·세부내역 행 → 칸 주소별 값(주소는 resolve로 찾은 것)."""
    unknown = [k for k in fields if k not in res.cells]
    if unknown:
        raise KeyError(f"양식에서 찾지 못한 칸: {unknown}")
    if len(rows) > res.item_count:
        raise ValueError(f"세부내역 {len(rows)}행 > 표 {res.item_count}행")
    out = {res.cells[k]: v for k, v in fields.items()}
    for n, row in enumerate(rows):
        for key in ITEM_KEYS:
            if row[key]:
                out[res.item_addr(n, key)] = [row[key]]
    return out


# ---------------------------------------------------------------- 파일 쓰기

def render(doc: RequestDoc | ReportDoc, path, row_mode: RowMode = "clone", template=None) -> Path:
    """문서를 hwpx로 저장. 템플릿의 다른 파일은 순서·압축 방식 그대로 복사한다."""
    is_request = isinstance(doc, RequestDoc)
    L = REQUEST if is_request else REPORT
    rows = item_rows(doc, row_mode)
    extra = max(0, len(rows) - MAX_LINE_ITEMS)
    fields = (request_fields if is_request else report_fields)(doc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with zipfile.ZipFile(template or L.template) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == SECTION:
                xml = expand_item_rows(data.decode("utf-8"), extra)
                values = cell_values(fields, rows, resolve(cells_from_xml(xml), L))
                data = fill_section(xml, values).encode("utf-8")
            dst.writestr(info, data, compress_type=info.compress_type)
    _write_preview(tmp)
    tmp.replace(path)
    return path


def _write_preview(path: Path) -> None:
    """미리보기 텍스트(Preview/PrvText.txt)를 채운 내용으로 바꾼다."""
    text = extract_text(path)
    tmp = path.with_name(path.name + ".prv")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = text.encode("utf-8") if info.filename == PREVIEW else src.read(info)
            dst.writestr(info, data, compress_type=info.compress_type)
    tmp.replace(path)
