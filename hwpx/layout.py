"""서식1(예산신청서)·서식2(집행보고서) 칸 매핑. 생성(fill)과 읽기(reader)가 함께 쓴다.

칸은 고정 주소가 아니라 라벨로 찾는다: 라벨 칸의 같은 행 오른쪽 첫 칸이 값 칸이다.
작성자가 표를 고쳐 열이 합쳐지거나(예: 8월 신앙캠프 보고서) 세부내역 행이 늘어난 문서도
같은 규칙으로 읽힌다. 빈 양식 기준 주소는 REQUEST_CELLS/REPORT_CELLS에 적어 두고 테스트로 확인한다.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from core.models import MAX_LINE_ITEMS
from core.paths import TEMPLATES_DIR

ITEM_FIRST_ROW = 16
ITEM_LAST_ROW = ITEM_FIRST_ROW + MAX_LINE_ITEMS - 1  # 22 (빈 양식 기준)
ITEM_KEYS = ("계정과목", "내용", "단가", "수량", "금액", "비고")
SENSITIVE = ("기안자연락처", "기안단체장연락처", "은행명", "계좌번호", "예금주")

# 필드 → 라벨(공백 제거). 값 칸 = 같은 행에서 라벨 오른쪽 첫 칸
FIELD_LABELS = {
    "문서번호": ("문서번호",), "행사명": ("행사명",), "주관단체": ("주관단체",), "협조단체": ("협조단체",),
    "기안자": ("기안자",), "기안단체장": ("기안단체장",), "목적": ("목적",), "일시": ("일시",),
    "장소": ("장소",), "은행명": ("은행명",), "계좌번호": ("계좌번호",), "예금주": ("예금주",),
    "참가대상": ("참가대상",), "인원": ("예상인원", "참가인원"),
    "수입": ("수입",), "자체예산": ("자체예산",), "본당보조": ("본당보조",), "예산A": ("예산",),
    "예산배정액": ("예산배정액(A)",), "금번신청액": ("금번신청액",),
    "신청누계액": ("신청누계액(B)",), "잔여액": ("잔여액(A-B)",),
}
# 라벨 행의 연락처 칸: (필드, 같은 행의 기준 라벨)
CONTACT_FIELDS = {"기안자연락처": "기안자", "기안단체장연락처": "기안단체장"}
# 세부내역 머리 행 라벨 → 키
ITEM_HEADERS = {"계정과목": "계정과목", "지출세부내용": "내용", "정산세부내용": "내용", "단가": "단가",
                "수량": "수량", "금액(원)": "금액", "비고": "비고"}
# 세부내역 아래 행(열 1 라벨) → 필드. 값은 금액 열
TOTAL_LABELS = {"합계": "합계", "합계(B)": "합계B", "잔액(A-B)": "잔액", "반납금액": "잔액"}

REQUEST_CELLS = {
    "작성일": (1, 0), "문서번호": (2, 1), "행사명": (3, 1), "주관단체": (5, 1), "협조단체": (5, 15),
    "기안자": (6, 1), "기안자연락처": (6, 15), "기안단체장": (7, 1), "기안단체장연락처": (7, 15),
    "목적": (8, 1), "일시": (9, 1), "장소": (10, 1), "은행명": (10, 6), "계좌번호": (10, 13),
    "예금주": (10, 26), "참가대상": (11, 1), "인원": (11, 9), "본문": (12, 1),
    "수입": (13, 1), "자체예산": (13, 19), "본당보조": (14, 19), "합계": (23, 18),
    "예산배정액": (24, 1), "금번신청액": (24, 5), "신청누계액": (24, 16), "잔여액": (24, 24),
}
REPORT_CELLS = {
    "작성일": (1, 0), "문서번호": (2, 1), "행사명": (3, 1), "주관단체": (5, 1), "협조단체": (5, 12),
    "기안자": (6, 1), "기안자연락처": (6, 12), "기안단체장": (7, 1), "기안단체장연락처": (7, 12),
    "목적": (8, 1), "일시": (9, 1), "장소": (10, 1), "은행명": (10, 4), "계좌번호": (10, 8),
    "예금주": (10, 19), "참가대상": (11, 1), "인원": (11, 10), "본문": (12, 1),
    "예산A": (13, 1), "자체예산": (13, 15), "본당보조": (14, 15), "합계B": (23, 13), "잔액": (24, 13),
}
REQUEST_ITEM_COLS = {"계정과목": 0, "내용": 1, "단가": 7, "수량": 12, "금액": 18, "비고": 23}
REPORT_ITEM_COLS = {"계정과목": 0, "내용": 1, "단가": 6, "수량": 8, "금액": 13, "비고": 18}


@dataclass(frozen=True)
class FormLayout:
    kind: str           # "예산신청서" | "집행보고서"
    title: str          # (0, 0) 칸 제목(공백 제거)
    template: Path
    fields: tuple       # 이 서식에 있는 필드


REQUEST = FormLayout("예산신청서", "예산신청서", TEMPLATES_DIR / "request_template.hwpx",
                     tuple(REQUEST_CELLS))
REPORT = FormLayout("집행보고서", "예산집행보고서", TEMPLATES_DIR / "report_template.hwpx",
                    tuple(REPORT_CELLS))
LAYOUTS = (REQUEST, REPORT)


@dataclass
class Resolved:
    """문서 하나의 실제 칸 주소."""
    cells: dict          # 필드 → (행, 열)
    item_cols: dict      # 세부내역 키 → 열
    first_item_row: int
    total_row: int       # 합계 행(세부내역 다음 행)
    missing: list        # 찾지 못한 필드

    @property
    def item_count(self) -> int:
        return self.total_row - self.first_item_row

    def item_addr(self, index: int, key: str) -> tuple[int, int]:
        return (self.first_item_row + index, self.item_cols[key])


def squash(s) -> str:
    if isinstance(s, list):
        s = " ".join(s)
    return re.sub(r"\s+", "", s or "")


def resolve(cells: dict, layout: FormLayout) -> Resolved:
    """cells: (행, 열) → 문단 목록. 라벨로 각 필드의 값 칸을 찾는다."""
    by_row: dict[int, list[int]] = {}
    for r, c in cells:
        by_row.setdefault(r, []).append(c)
    for cols in by_row.values():
        cols.sort()
    labels = {(r, c): squash(v) for (r, c), v in cells.items()}

    def right_of(r, c):
        nxt = [x for x in by_row[r] if x > c]
        return (r, nxt[0]) if nxt else None

    def find(label, row=None):
        return next(((r, c) for (r, c), t in sorted(labels.items())
                     if t == label and (row is None or r == row)), None)

    out, missing = {"작성일": (1, 0)}, []
    for name, names in FIELD_LABELS.items():
        if name not in layout.fields:
            continue
        hit = next((a for n in names if (a := find(n))), None)
        addr = right_of(*hit) if hit else None
        if addr:
            out[name] = addr
        else:
            missing.append(name)
    for name, anchor in CONTACT_FIELDS.items():
        hit = find("연락처", out[anchor][0]) if anchor in out else None
        if hit and (addr := right_of(*hit)):
            out[name] = addr
        else:
            missing.append(name)
    if "참가대상" in out:  # 본문 칸: 참가대상 다음 행의 라벨 오른쪽
        r = out["참가대상"][0] + 1
        out["본문"] = right_of(r, by_row[r][0])

    header = next(r for (r, c), t in labels.items() if c == 0 and t == "계정과목")
    item_cols = {ITEM_HEADERS[t]: c for (r, c), t in labels.items() if r == header and t in ITEM_HEADERS}
    total_row = min(r for (r, c), t in labels.items() if r > header and c == 1 and t in TOTAL_LABELS)
    for (r, c), t in labels.items():
        if r >= total_row and c == 1 and t in TOTAL_LABELS and TOTAL_LABELS[t] in layout.fields:
            out[TOTAL_LABELS[t]] = (r, item_cols["금액"])
    missing += [k for k in ITEM_KEYS if k not in item_cols]
    missing += [f for f in layout.fields if f not in out and f not in missing]
    return Resolved(out, item_cols, header + 1, total_row, missing)
