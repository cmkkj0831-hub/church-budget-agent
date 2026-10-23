"""회계연도 전환: 지난해 마감(읽기 전용), 미해결 이슈 이월, 새 연도 장부 생성(문서번호 001부터, 누계 0)."""
from pathlib import Path

from core.issues import HISTORY_COLUMNS, ISSUE_COLUMNS, issue_to_csv
from core.ledger import LEDGER_COLUMNS, BlockedError, Ledger, load_settings, save_settings, write_csv
from core.models import Issue, Settings, YearInfo
from core.paths import year_dir
from core.plan_io import plan_problems, write_plan
from core.usage import USAGE_COLUMNS


def close_year(settings: Settings, year: int) -> None:
    settings.year_info(year).마감 = True


def carry_issue(old: Issue, prev_year: int, new_id: str, ledger_docs: list[str]) -> Issue:
    """지난해 이슈를 새 연도로 옮긴 사본(새 연도 장부에는 대상 행이 없으므로 대상순번은 비운다)."""
    return Issue(
        이슈ID=new_id, 대상순번=[], 대상문서번호=[f"{prev_year} {d}" for d in ledger_docs] or [f"{prev_year}"],
        유형=old.유형, 설명=f"[{prev_year} 이월] {old.설명}",
        근거=f"{prev_year}년 {old.이슈ID}에서 이월. 원래 근거: {old.근거}", 상태="미해결",
        검토메모=old.검토메모)


def start_year(data_dir, new_year: int, plan: list[dict], plan_info: list[dict], allocation: int,
               carry: list[Issue] = (), prev_year: int | None = None, force: bool = False) -> Settings:
    """data/{new_year}/를 만들고 설정을 새 연도로 바꾼다. 지난해는 마감한다. 바뀐 설정을 돌려준다(저장까지 함)."""
    data_dir = Path(data_dir)
    settings = load_settings(data_dir)
    prev_year = prev_year or settings.현재연도
    yd = year_dir(new_year, data_dir)
    if (yd / "ledger.csv").exists() and not force:
        raise BlockedError(f"{new_year}년 장부가 이미 있습니다")
    problems = [p for p in plan_problems(plan) if "배정액" not in p]
    if problems:
        raise BlockedError("사업계획서 검증 실패: " + "; ".join(problems))
    if not plan:
        raise BlockedError("사업계획서가 비어 있습니다")

    write_plan(yd, plan, plan_info)
    write_csv(yd / "ledger.csv", LEDGER_COLUMNS, [])
    prev_rows = {r.순번: r for r in Ledger.load(data_dir, prev_year).rows} if carry else {}
    issues = [carry_issue(old, prev_year, f"I-{n:03d}",
                          [prev_rows[s].문서번호 for s in old.대상순번 if s in prev_rows])
              for n, old in enumerate(carry, 1)]
    write_csv(yd / "issues.csv", ISSUE_COLUMNS, [issue_to_csv(i) for i in issues])
    write_csv(yd / "history.csv", HISTORY_COLUMNS, [])
    write_csv(yd / "usage.csv", USAGE_COLUMNS, [])

    if str(prev_year) in settings.연도별:
        close_year(settings, prev_year)
    settings.연도별[str(new_year)] = YearInfo(예산배정액=allocation, 다음문서번호=1)
    settings.기본정보.회계연도 = new_year
    save_settings(settings, data_dir)
    return settings
