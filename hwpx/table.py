"""hwpx 표 행 복제. 세부내역 마지막 행을 복제해 칸을 늘린다."""
import re

from hwpx.layout import ITEM_LAST_ROW

_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_TR = re.compile(r"<hp:tr>.*?</hp:tr>", re.S)


def _cell_info(tc: str) -> tuple[int, int, int]:
    row = int(re.search(r'rowAddr="(\d+)"', tc).group(1))
    span = int(re.search(r'rowSpan="(\d+)"', tc).group(1))
    height = int(re.search(r'<hp:cellSz [^>]*height="(\d+)"', tc).group(1))
    return row, span, height


def _set_attr(tag_text: str, attr: str, value) -> str:
    return re.sub(rf'{attr}="\d+"', f'{attr}="{value}"', tag_text, count=1)


def _set_height(tc: str, height: int) -> str:
    return re.sub(r'(<hp:cellSz [^>]*height=")\d+(")', rf"\g<1>{height}\g<2>", tc, count=1)


def expand_item_rows(xml: str, extra: int, last_row: int = ITEM_LAST_ROW) -> str:
    """세부내역 마지막 행(last_row)을 extra번 복제한다.

    - 복제 행과 그 아래 칸의 rowAddr를 밀고, 표 rowCnt·높이를 늘린다.
    - last_row를 지나 걸친 병합 칸(rowSpan)은 rowSpan과 높이를 함께 늘린다.
    - 표가 한 쪽을 넘을 수 있으므로 쪽 나눔을 셀 단위(CELL)로 바꾸고,
      표 뒤 문단의 줄 배치(linesegarray)는 지워 한글이 다시 배치하게 한다.
    """
    if extra <= 0:
        return xml
    start = xml.index("<hp:tbl ")
    end = xml.index("</hp:tbl>") + len("</hp:tbl>")
    table = xml[start:end]
    if table.count("<hp:tbl ") != 1:
        raise ValueError("중첩 표는 지원하지 않음")

    rows = _TR.findall(table)
    source = next(tr for tr in rows if any(_cell_info(tc)[0] == last_row for tc in _TC.findall(tr)))
    heights = [h for r, s, h in map(_cell_info, _TC.findall(source)) if s == 1]
    if any(s > 1 for _, s, _ in map(_cell_info, _TC.findall(source))):
        raise ValueError("복제할 행에서 아래로 병합된 칸은 지원하지 않음")
    row_h = max(heights)

    def shift(tc: str) -> str:
        row, span, height = _cell_info(tc)
        if row > last_row:
            return _set_attr(tc, "rowAddr", row + extra)
        if span > 1 and row < last_row <= row + span - 1:
            tc = _set_attr(tc, "rowSpan", span + extra)
            return _set_height(tc, height + extra * row_h)
        return tc

    def clone(k: int) -> str:
        return _TC.sub(lambda m: _set_attr(m.group(), "rowAddr", last_row + k), source)

    new_rows = []
    for tr in rows:
        new_rows.append(_TC.sub(lambda m: shift(m.group()), tr))
        if tr is source:
            new_rows.extend(clone(k) for k in range(1, extra + 1))

    head = table[:table.index("<hp:tr>")]
    tail = table[table.rindex("</hp:tr>") + len("</hp:tr>"):]
    count = int(re.search(r'rowCnt="(\d+)"', head).group(1))
    head = _set_attr(head, "rowCnt", count + extra)
    sz = re.search(r'<hp:sz [^>]*height="(\d+)"', head)
    head = head[:sz.start()] + re.sub(r'height="\d+"', f'height="{int(sz.group(1)) + extra * row_h}"',
                                      sz.group(), count=1) + head[sz.end():]
    head = head.replace('pageBreak="NONE"', 'pageBreak="CELL"', 1)
    after = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", xml[end:], flags=re.S)
    return xml[:start] + head + "".join(new_rows) + tail + after
