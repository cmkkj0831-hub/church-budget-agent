"""이슈 해결. 장부 변경은 이 경로로만 하고, 전후 값을 ledger_history.csv에 남긴다."""
from datetime import date, datetime

from core.ledger import (
    HISTORY_COLUMNS, ISSUE_COLUMNS, ISSUE_STATES, LEDGER_COLUMNS, OPEN_STATES,
    load_history, load_issues, load_ledger, target_seqs, write_csv,
)
from core.paths import HISTORY_CSV, ISSUES_CSV, LEDGER_CSV


def resolve_issue(issue_id: str, state: str, method: str, content: str, handler: str,
                  changes: list[tuple[int, str, object]] = (), on: date | None = None) -> None:
    """이슈를 해결 상태로 바꾸고, changes=[(순번, 열, 새 값)]가 있으면 장부를 정정한다."""
    if state not in ISSUE_STATES or state in OPEN_STATES:
        raise ValueError(f"해결 상태가 아님: {state}")
    if state == "해결-정정" and not changes:
        raise ValueError("해결-정정에는 장부 변경이 필요하다")

    issues = load_issues()
    issue = next((i for i in issues if i["이슈ID"] == issue_id), None)
    if issue is None:
        raise KeyError(issue_id)
    if issue["상태"] not in OPEN_STATES:
        raise ValueError(f"{issue_id}는 이미 {issue['상태']}")

    ledger = load_ledger()
    by_seq = {r["순번"]: r for r in ledger}
    history = load_history()
    now = datetime.now().isoformat(timespec="seconds")
    for seq, col, new in changes:
        if seq not in target_seqs(issue):
            raise ValueError(f"순번 {seq}는 {issue_id}의 대상이 아님")
        if col not in LEDGER_COLUMNS or col == "순번":
            raise ValueError(f"변경 불가 열: {col}")
        row = by_seq[seq]
        old = row[col]
        row[col] = new
        history.append({
            "이력ID": f"H-{len(history) + 1:04d}", "일시": now, "이슈ID": issue_id,
            "순번": seq, "열": col, "변경전": "" if old is None else old, "변경후": new,
            "사유": content, "처리자": handler,
        })

    issue.update({"상태": state, "해결방식": method, "해결내용": content,
                  "처리자": handler, "처리일": (on or date.today()).isoformat()})

    if changes:
        write_csv(LEDGER_CSV, LEDGER_COLUMNS, ledger)
        write_csv(HISTORY_CSV, HISTORY_COLUMNS, history)
    write_csv(ISSUES_CSV, ISSUE_COLUMNS, issues)
