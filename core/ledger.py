"""장부·이슈·이력 CSV 스키마와 입출력, 비고(파생값) 계산."""
import csv
import re
from datetime import date

from core.paths import HISTORY_CSV, ISSUES_CSV, LEDGER_CSV

LEDGER_COLUMNS = [
    "순번", "월", "구분", "작성일", "예산배정액", "금번신청액", "신청누계액", "잔여액",
    "자체예산", "본당보조", "합계", "잔액", "문서번호", "행사명", "파일명",
    "사업ID", "연결문서번호", "상태",
]
LEDGER_INT_COLUMNS = [
    "순번", "예산배정액", "금번신청액", "신청누계액", "잔여액",
    "자체예산", "본당보조", "합계", "잔액",
]

ISSUE_COLUMNS = [
    "이슈ID", "대상순번", "대상문서번호", "유형", "설명", "근거", "관련이슈", "상태",
    "해결방식", "해결내용", "첨부", "처리자", "처리일",
]
ISSUE_TYPES = ["계산 불일치", "누계 누락", "기재 오류", "문서 누락", "번호 중복", "미확인"]
ISSUE_STATES = ["미해결", "확인중", "해결-정정", "해결-설명됨"]
OPEN_STATES = {"미해결", "확인중"}

HISTORY_COLUMNS = ["이력ID", "일시", "이슈ID", "순번", "열", "변경전", "변경후", "사유", "처리자"]

# 대상순번·대상문서번호가 여러 개일 때 구분자
MULTI_SEP = ";"


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in columns})


def to_int(v):
    """빈칸은 None(공란 그대로), 나머지는 int."""
    if v is None or str(v).strip() == "":
        return None
    return int(str(v).replace(",", ""))


def load_ledger() -> list[dict]:
    rows = read_csv(LEDGER_CSV)
    for r in rows:
        for c in LEDGER_INT_COLUMNS:
            r[c] = to_int(r[c])
    return rows


def load_issues() -> list[dict]:
    return read_csv(ISSUES_CSV)


def load_history() -> list[dict]:
    return read_csv(HISTORY_CSV)


def parse_date(s) -> date | None:
    """'2026년 07월 04일' 같은 원본 표기를 date로. 해석 불가하면 None."""
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", str(s or ""))
    return date(*map(int, m.groups())) if m else None


def target_seqs(issue: dict) -> list[int]:
    return [int(x) for x in str(issue["대상순번"]).split(MULTI_SEP) if x.strip()]


def derive_remarks(issues: list[dict]) -> dict[int, str]:
    """비고 = 해당 순번을 대상으로 하는 열린 이슈의 설명. 해결되면 사라진다."""
    remarks: dict[int, list[str]] = {}
    for i in issues:
        if i["상태"] in OPEN_STATES:
            for seq in target_seqs(i):
                remarks.setdefault(seq, []).append(i["설명"])
    return {k: " / ".join(v) for k, v in remarks.items()}
