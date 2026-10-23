"""반영 단위 이력과 되돌리기.

앱의 모든 장부·이슈 변경은 '반영' 하나로 묶어 기록한다: 반영 전 상태(snapshot)와 반영 후 상태를 비교해
바뀐 칸·추가/삭제된 행·이슈를 history.csv에 같은 이력ID로 남긴다. 되돌리기는 그 반영의 항목을 거꾸로
적용하되, 지금 값이 그 반영 직후 값과 같을 때만 한다(이후 변경과 겹치면 거부). 되돌리기 자체도 새 반영으로 기록된다.
"""
import json
from datetime import datetime

from core.issues import IssueBook, issue_from_csv, issue_to_csv
from core.ledger import LEDGER_INT_COLUMNS, BlockedError, Ledger
from core.models import HistoryEntry, LedgerRow

ROW_ADD, ROW_DEL, ISSUE_ADD = "(행 추가)", "(행 삭제)", "(이슈 추가)"
UNDO_PREFIX = "되돌리기: "


def _s(v) -> str:
    return "" if v is None else str(v)


def snapshot(ledger: Ledger, book: IssueBook) -> dict:
    return {"rows": {r.순번: r.model_dump() for r in ledger.rows},
            "issues": {i.이슈ID: issue_to_csv(i) for i in book.issues}}


def diff(base: dict, ledger: Ledger, book: IssueBook) -> list[dict]:
    now = snapshot(ledger, book)
    out = []
    for seq, row in now["rows"].items():
        old = base["rows"].get(seq)
        if old is None:
            out.append({"대상": "장부", "순번": seq, "열": ROW_ADD, "변경전": "", "변경후": json.dumps(row, ensure_ascii=False)})
            continue
        for col, val in row.items():
            if _s(old.get(col)) != _s(val):
                out.append({"대상": "장부", "순번": seq, "열": col, "변경전": _s(old.get(col)), "변경후": _s(val)})
    for seq, old in base["rows"].items():
        if seq not in now["rows"]:
            out.append({"대상": "장부", "순번": seq, "열": ROW_DEL, "변경전": json.dumps(old, ensure_ascii=False), "변경후": ""})
    for iid, issue in now["issues"].items():
        old = base["issues"].get(iid)
        if old is None:
            out.append({"대상": "이슈", "이슈ID": iid, "열": ISSUE_ADD, "변경전": "",
                        "변경후": json.dumps(issue, ensure_ascii=False)})
            continue
        for col, val in issue.items():
            if _s(old.get(col)) != _s(val):
                out.append({"대상": "이슈", "이슈ID": iid, "열": col, "변경전": _s(old.get(col)), "변경후": _s(val)})
    return out


def record_unit(ledger: Ledger, book: IssueBook, base: dict, 사유: str, 처리자: str,
                이슈ID: str = "", when: datetime | None = None) -> str | None:
    """base 이후 바뀐 것을 한 반영(같은 이력ID)으로 history에 남긴다. 바뀐 게 없으면 None."""
    changes = diff(base, ledger, book)
    if not changes:
        return None
    unit = book.next_history_id()
    stamp = (when or datetime.now()).isoformat(timespec="seconds")
    for c in changes:
        book.history.append(HistoryEntry(
            이력ID=unit, 일시=stamp, 대상=c["대상"], 이슈ID=c.get("이슈ID") or 이슈ID, 순번=c.get("순번"),
            열=c["열"], 변경전=c["변경전"], 변경후=c["변경후"], 사유=사유, 처리자=처리자))
    return unit


def units(book: IssueBook) -> list[dict]:
    """반영 단위 목록(최근 것부터): {이력ID, 일시, 사유, 처리자, 항목수, 되돌림}."""
    undone = {h.사유[len(UNDO_PREFIX):] for h in book.history if h.사유.startswith(UNDO_PREFIX)}
    seen: dict[str, dict] = {}
    for h in book.history:
        u = seen.setdefault(h.이력ID, {"이력ID": h.이력ID, "일시": h.일시, "사유": h.사유, "처리자": h.처리자,
                                       "항목수": 0, "되돌림": h.이력ID in undone})
        u["항목수"] += 1
    return sorted(seen.values(), key=lambda u: u["이력ID"], reverse=True)


def _typed(col: str, value: str):
    if col in LEDGER_INT_COLUMNS:
        return None if value == "" else int(value)
    return value


def undo_unit(ledger: Ledger, book: IssueBook, unit: str, 처리자: str, when: datetime | None = None) -> str:
    """반영 하나를 되돌린다. 지금 값이 그 반영 직후와 다르면(이후 변경과 겹치면) BlockedError."""
    entries = [h for h in book.history if h.이력ID == unit]
    if not entries:
        raise BlockedError(f"{unit} 이력이 없습니다")
    if any(h.사유 == UNDO_PREFIX + unit for h in book.history):
        raise BlockedError(f"{unit}은(는) 이미 되돌렸습니다")
    rows = {r.순번: r for r in ledger.rows}
    issues = {i.이슈ID: i for i in book.issues}
    conflicts = []
    for h in entries:
        if h.대상 == "이슈":
            cur = issues.get(h.이슈ID)
            if h.열 == ISSUE_ADD:
                ok = cur is not None
            else:
                ok = cur is not None and _s(issue_to_csv(cur).get(h.열)) == h.변경후
        elif h.열 == ROW_ADD:
            ok = h.순번 in rows and json.dumps(rows[h.순번].model_dump(), ensure_ascii=False) == h.변경후
        elif h.열 == ROW_DEL:
            ok = h.순번 not in rows
        else:
            ok = h.순번 in rows and _s(getattr(rows[h.순번], h.열)) == h.변경후
        if not ok:
            conflicts.append(f"{h.대상} {h.이슈ID or h.순번} {h.열}")
    if conflicts:
        raise BlockedError(f"{unit} 이후에 같은 칸이 다시 바뀌어 되돌릴 수 없습니다: " + ", ".join(conflicts))

    base = snapshot(ledger, book)
    for h in reversed(entries):
        if h.대상 == "이슈":
            if h.열 == ISSUE_ADD:
                book.issues = [i for i in book.issues if i.이슈ID != h.이슈ID]
            else:
                idx = next(k for k, i in enumerate(book.issues) if i.이슈ID == h.이슈ID)
                d = issue_to_csv(book.issues[idx])
                d[h.열] = h.변경전
                book.issues[idx] = issue_from_csv(d)
        elif h.열 == ROW_ADD:
            ledger.rows = [r for r in ledger.rows if r.순번 != h.순번]
        elif h.열 == ROW_DEL:
            ledger.rows.append(LedgerRow(**json.loads(h.변경전)))
            ledger.rows.sort(key=lambda r: r.순번)
        else:
            setattr(next(r for r in ledger.rows if r.순번 == h.순번), h.열, _typed(h.열, h.변경전))
    new = record_unit(ledger, book, base, UNDO_PREFIX + unit, 처리자, when=when)
    return new
