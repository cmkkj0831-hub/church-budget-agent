"""배포 전후 점검: budget_env/bin/python scripts/check_cloud.py [--secrets 경로] [--skip-claude]

secrets(기본 .streamlit/secrets.toml, 같은 이름의 환경변수가 우선)로 아래를 차례로 점검하고 표로 보여 준다.
비밀값은 출력하지 않는다. 운영 데이터(DATA_BRANCH)는 읽기만 하고, 쓰기 시험은 임시 브랜치에서 한 뒤 지운다.

  1. GitHub: 저장소·DATA_BRANCH 읽기(장부 행 수 등), 임시 브랜치 쓰기·삭제
  2. SETTINGS_KEY: settings_private.enc 복호화(없으면 임시 파일로 암호화 왕복만)
  3. Claude API 1회 호출(모델명·토큰)
  4. hwpx 생성·읽기 왕복, 연간 결산 xlsx 내보내기
"""
import argparse
import os
import sys
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.issues import IssueBook  # noqa: E402
from core.ledger import Ledger, load_settings, read_csv  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402
from core.private import PrivateError, PrivateSettings, load_private, save_private  # noqa: E402
from core.secrets import SECRETS_TOML, load_secrets  # noqa: E402

KEYS = ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "DATA_BRANCH", "SETTINGS_KEY", "ADMIN_PASSWORD",
        "VIEWER_PASSWORD", "APP_ENV")
rows: list[tuple[str, str, str]] = []


def record(item: str, ok: bool | None, detail: str) -> None:
    rows.append((item, "통과" if ok else "건너뜀" if ok is None else "실패", detail))


def step(item: str):
    """예외를 '실패'로 기록하는 데코레이터. 예외 메시지에 비밀값이 섞이지 않도록 형식만 남긴다."""
    def wrap(fn):
        def run(*a, **kw):
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                record(item, False, f"{type(e).__name__}: {str(e)[:160]}")
                if os.environ.get("CHECK_DEBUG"):
                    traceback.print_exc()
        return run
    return wrap


def load(path: Path) -> dict:
    s = load_secrets(path)
    for k in KEYS:
        if os.environ.get(k):
            s[k] = os.environ[k]
    return s


def secrets_status(s: dict) -> None:
    """필수: API 키·SETTINGS_KEY·ADMIN_PASSWORD·APP_ENV. GITHUB_TOKEN이 있으면(클라우드) GITHUB_REPO·DATA_BRANCH도."""
    required = ["ANTHROPIC_API_KEY", "SETTINGS_KEY", "ADMIN_PASSWORD", "APP_ENV"]
    if s.get("GITHUB_TOKEN"):
        required += ["GITHUB_REPO", "DATA_BRANCH"]
    have = [k for k in KEYS if s.get(k)]
    missing = [k for k in required if not s.get(k)]
    record("secrets 항목", not missing, f"있음 {', '.join(have) or '-'}" + (f" / 없음 {', '.join(missing)}" if missing else ""))
    if s.get("APP_ENV") and s["APP_ENV"] not in ("dev", "prod"):
        record("APP_ENV", False, "dev 또는 prod여야 합니다")
    elif s.get("GITHUB_TOKEN") and s.get("APP_ENV") != "prod":
        record("APP_ENV", False, "클라우드 운영은 APP_ENV = \"prod\"")


@step("GitHub 읽기")
def github_read(s: dict) -> Path | None:
    if not s.get("GITHUB_TOKEN"):
        record("GitHub 읽기", None, "GITHUB_TOKEN 없음 → 로컬 data/ 모드로 점검")
        return None
    from github import Auth, Github

    from core.storage import GitHubStorage
    repo = Github(auth=Auth.Token(s["GITHUB_TOKEN"])).get_repo(s["GITHUB_REPO"])
    branch = s.get("DATA_BRANCH", "data")
    notes = []
    if branch == repo.default_branch:
        notes.append(f"⚠️ DATA_BRANCH가 배포 브랜치({branch})와 같음 → 저장할 때마다 앱이 재시작됨")
    if not repo.private:
        notes.append("⚠️ 공개 저장소 — 교사명단·장부가 공개됨. 비공개로 바꾸세요")
    ws = GitHubStorage(repo, branch).checkout()
    settings = load_settings(ws.path)
    y = settings.현재연도
    L = Ledger.load(ws.path, y)
    enc = "있음" if (ws.path / "settings_private.enc").exists() else "없음"
    record("GitHub 읽기", not notes,
           f"{repo.full_name}@{branch}: 파일 {len(ws.content)}개, {y}년 장부 {len(L.rows)}행, "
           f"다음 문서번호 {L.next_doc_number()}, settings_private.enc {enc}" + (" / " + " / ".join(notes) if notes else ""))
    return ws.path


@step("GitHub 쓰기·삭제")
def github_write(s: dict) -> None:
    if not s.get("GITHUB_TOKEN"):
        record("GitHub 쓰기·삭제", None, "GITHUB_TOKEN 없음")
        return
    from github import Auth, Github

    from core.storage import GitHubStorage
    repo = Github(auth=Auth.Token(s["GITHUB_TOKEN"])).get_repo(s["GITHUB_REPO"])
    base = repo.get_branch(s.get("DATA_BRANCH", "data")).commit.sha
    name = f"check-cloud-{datetime.now():%Y%m%d-%H%M%S}"
    repo.create_git_ref(f"refs/heads/{name}", base)  # 운영 브랜치는 건드리지 않는다
    try:
        st = GitHubStorage(repo, name)
        ws = st.checkout()
        (ws.path / "_check_cloud.txt").write_text(f"점검 {datetime.now().isoformat()}\n", encoding="utf-8")
        changed = st.commit(ws, "점검: 쓰기 시험(임시 브랜치)")
        ok = changed == ["_check_cloud.txt"]
    finally:
        repo.get_git_ref(f"heads/{name}").delete()
    gone = name not in [b.name for b in repo.get_branches()]
    record("GitHub 쓰기·삭제", ok and gone, f"임시 브랜치 {name}에 커밋 {'성공' if ok else '실패'}, 삭제 {'완료' if gone else '안 됨'}")


@step("SETTINGS_KEY")
def settings_key(s: dict, data_dir: Path) -> None:
    key = s.get("SETTINGS_KEY", "")
    if not key:
        record("SETTINGS_KEY", False, "없음 — scripts/gen_settings_key.py로 만드세요")
        return
    enc = data_dir / "settings_private.enc"
    if enc.exists():
        try:
            p = load_private(key, enc)
        except PrivateError as e:
            record("SETTINGS_KEY", False, f"복호화 실패({e}) — 키가 바뀌었거나 다른 배포의 파일")
            return
        filled = [n for n, v in (("비밀번호 해시", p.관리자비밀번호해시), ("기안자 연락처", p.연락처.기안자),
                                 ("기안단체장 연락처", p.연락처.기안단체장), ("은행명", p.계좌.은행명),
                                 ("계좌번호", p.계좌.계좌번호), ("예금주", p.계좌.예금주)) if v]
        record("SETTINGS_KEY", True, f"settings_private.enc 복호화 성공, 채워진 칸: {', '.join(filled) or '없음'}")
        return
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "settings_private.enc"
        p = PrivateSettings()
        p.계좌.예금주 = "점검"
        save_private(p, key, path)
        ok = load_private(key, path).계좌.예금주 == "점검" and b"\xec\xa0\x90" not in path.read_bytes()
    record("SETTINGS_KEY", ok, "settings_private.enc 없음(첫 접속 전) → 임시 파일 암호화 왕복 성공")


@step("Claude API")
def claude(s: dict, data_dir: Path) -> None:
    if not s.get("ANTHROPIC_API_KEY"):
        record("Claude API", False, "ANTHROPIC_API_KEY 없음")
        return
    import anthropic
    model = load_settings(data_dir).모델
    r = anthropic.Anthropic(api_key=s["ANTHROPIC_API_KEY"]).messages.create(
        model=model, max_tokens=16, messages=[{"role": "user", "content": "'확인'이라고만 답하세요."}])
    key = s["ANTHROPIC_API_KEY"]
    record("Claude API", True, f"요청 {model} → 응답 {r.model}, 토큰 입력 {r.usage.input_tokens}/출력 "
           f"{r.usage.output_tokens}, 키 끝 4자리 {key[-4:]}")


@step("hwpx 왕복")
def hwpx_round_trip(data_dir: Path) -> None:
    from core.models import LineItem, RequestDoc
    from hwpx.fill import render
    from hwpx.reader import read_document
    L = Ledger.load(data_dir).copy()  # 메모리 사본 — 저장하지 않는다
    y = L.year
    draft = RequestDoc(월=12, 사업ID=next(iter(L.plan)), 행사명="점검용 문서", 작성일=date(y, 12, 1),
                       시작=datetime(y, 12, 1, 10), 종료=datetime(y, 12, 1, 12), 장소="점검", 목적="점검",
                       세부내역=[LineItem(계정과목="점검", 내용="항목", 단가=1_000, 수량=3)])
    doc = L.build_request(draft, today=date(y, 12, 1)).docs[0]
    with tempfile.TemporaryDirectory() as d:
        res = read_document(render(doc, Path(d) / "check.hwpx"), 사업ID=doc.사업ID)
    back = res.doc
    ok = (back.행사명, back.합계, back.신청누계액, back.문서번호) == (doc.행사명, doc.합계, doc.신청누계액, doc.문서번호)
    record("hwpx 왕복", ok and not res.failures,
           f"{doc.문서번호} 합계 {doc.합계:,}원 생성·읽기 {'일치' if ok else '불일치'}"
           + (f", 못 읽은 칸 {res.failures}" if res.failures else ""))


@step("xlsx 내보내기")
def xlsx_export(data_dir: Path) -> None:
    from openpyxl import load_workbook

    from core.export import export_annual_xlsx
    L, B = Ledger.load(data_dir), IssueBook.load(data_dir)
    y = L.year
    info, usage = data_dir / str(y) / "plan_info.csv", data_dir / str(y) / "usage.csv"
    with tempfile.TemporaryDirectory() as d:
        path = export_annual_xlsx(L, B, read_csv(info) if info.exists() else [],
                                  read_csv(usage) if usage.exists() else [], Path(d) / "check.xlsx")
        sheets = load_workbook(path, read_only=True).sheetnames
    record("xlsx 내보내기", len(sheets) >= 4, f"시트 {len(sheets)}개: {', '.join(sheets)}")


def table() -> str:
    w = [max(len(r[i]) for r in rows + [("항목", "결과", "내용")]) for i in range(2)]
    lines = [f"{'항목'.ljust(w[0])} | {'결과'.ljust(w[1])} | 내용", "-" * (w[0] + w[1] + 30)]
    lines += [f"{a.ljust(w[0])} | {b.ljust(w[1])} | {c}" for a, b, c in rows]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default=str(SECRETS_TOML))
    ap.add_argument("--skip-claude", action="store_true")
    a = ap.parse_args()
    s = load(Path(a.secrets))
    secrets_status(s)
    remote = github_read(s)
    github_write(s)
    data_dir = remote or Path(os.environ.get("BUDGET_DATA_DIR") or s.get("DATA_DIR") or DATA_DIR)
    if remote is None:
        record("데이터 위치", True, f"로컬 {data_dir}")
    settings_key(s, data_dir)
    if a.skip_claude:
        record("Claude API", None, "--skip-claude")
    else:
        claude(s, data_dir)
    hwpx_round_trip(data_dir)
    xlsx_export(data_dir)
    print(table())
    return 1 if any(r[1] == "실패" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
