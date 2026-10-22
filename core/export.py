"""장부 + 이슈 → 회계내역 xlsx (원본 회계내역 서식)."""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from core.issues import ISSUE_COLUMNS, IssueBook, issue_to_csv
from core.ledger import Ledger

FONT = "맑은 고딕"
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
REQUEST_FILL = PatternFill("solid", fgColor="FFFFFF")
REPORT_FILL = PatternFill("solid", fgColor="EAF1F8")
RED = "C00000"
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = "#,##0;-#,##0;0"

# (헤더, 장부 열 또는 '비고', 너비)
LEDGER_SHEET = [
    ("순번", "순번", 6), ("월", "월", 6), ("구분", "구분", 11), ("작성일", "작성일", 15),
    ("예산 배정액", "예산배정액", 13), ("금번 신청액", "금번신청액", 12),
    ("신청 누계액", "신청누계액", 13), ("잔여액", "잔여액", 13), ("자체 예산", "자체예산", 11),
    ("본당 보조", "본당보조", 12), ("합계", "합계", 12), ("잔액", "잔액", 10),
    ("문서번호", "문서번호", 14), ("행사명", "행사명", 30), ("비고", "비고", 60),
    ("파일명", "파일명", 34), ("사업ID", "사업ID", 7), ("연결문서번호", "연결문서번호", 14),
    ("상태", "상태", 10),
]
MONEY_COLS = {"예산배정액", "금번신청액", "신청누계액", "잔여액", "자체예산", "본당보조", "합계", "잔액"}
CENTER_COLS = {"순번", "월", "구분", "작성일", "문서번호", "사업ID", "연결문서번호", "상태"}
ISSUE_WIDTHS = {"설명": 50, "근거": 60, "해결내용": 40, "대상문서번호": 28}
STATE_FONT = {"미해결": RED, "확인중": "C65911"}


def _header(ws, headers, widths):
    for c, (h, w) in enumerate(zip(headers, widths), 1):
        cell = ws.cell(1, c, h)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(c)].width = w


def export_xlsx(ledger: Ledger, book: IssueBook, path) -> Path:
    remarks = book.ledger_remarks()
    wb = Workbook()
    ws = wb.active
    ws.title = f"{ledger.settings.기본정보.회계연도} 신청·집행 내역"
    _header(ws, [h for h, _, _ in LEDGER_SHEET], [w for _, _, w in LEDGER_SHEET])

    for i, r in enumerate(ledger.rows, 2):
        fill = REQUEST_FILL if r.신청서 else REPORT_FILL
        for c, (_, col, _) in enumerate(LEDGER_SHEET, 1):
            value = remarks.get(r.순번, "") if col == "비고" else getattr(r, col)
            cell = ws.cell(i, c, value if value != "" else None)
            cell.fill = fill
            cell.border = BORDER
            cell.font = Font(name=FONT, size=10, color=RED if col == "비고" else None)
            if col in MONEY_COLS:
                cell.number_format = MONEY
            if col in CENTER_COLS:
                cell.alignment = Alignment(horizontal="center")
            if col == "비고":
                cell.alignment = Alignment(wrap_text=True, vertical="center")
    last = len(ledger.rows) + 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(LEDGER_SHEET))}{last}"
    ws.freeze_panes = "E2"
    note = ws.cell(last + 2, 1, "※ 흰색 행 = 예산신청서, 파란색 행 = 집행보고서. "
                                "비고(빨간 글씨)는 열린 이슈의 설명이며, 이슈가 해결되면 사라진다.")
    note.font = Font(name=FONT, size=9, color="595959")

    wi = wb.create_sheet("이슈")
    _header(wi, ISSUE_COLUMNS, [ISSUE_WIDTHS.get(c, 12) for c in ISSUE_COLUMNS])
    for i, issue in enumerate(book.issues, 2):
        d = issue_to_csv(issue)
        for c, col in enumerate(ISSUE_COLUMNS, 1):
            cell = wi.cell(i, c, d[col] or None)
            cell.border = BORDER
            cell.font = Font(name=FONT, size=10,
                             color=STATE_FONT.get(issue.상태) if col == "상태" else None)
            cell.alignment = Alignment(wrap_text=col in ISSUE_WIDTHS, vertical="center")
    wi.auto_filter.ref = f"A1:{get_column_letter(len(ISSUE_COLUMNS))}{len(book.issues) + 1}"
    wi.freeze_panes = "B2"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


if __name__ == "__main__":
    from core.paths import OUT_DIR
    out = export_xlsx(Ledger.load(), IssueBook.load(), OUT_DIR / "2026_초등부교사회_회계내역.xlsx")
    print(out)
