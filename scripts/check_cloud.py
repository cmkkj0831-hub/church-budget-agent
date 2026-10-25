"""배포 전후 점검: budget_env/bin/python scripts/check_cloud.py [--secrets 경로] [--skip-claude]

secrets(기본 .streamlit/secrets.toml, 같은 이름의 환경변수가 우선)로 아래를 차례로 점검하고 표로 보여 준다.
비밀값은 출력하지 않는다. 운영 데이터(DATA_BRANCH)는 읽기만 하고, 쓰기 시험은 임시 브랜치에서 한 뒤 지운다.

  1. GitHub: 저장소·DATA_BRANCH 읽기(settings.json), 임시 브랜치 쓰기·삭제
  2. 설정: settings.json 읽기, 연락처·계좌 칸이 채워졌는지(값은 출력하지 않음)
  3. Claude API 1회 호출(모델명·토큰)
  4. 집행보고서 hwpx 생성·읽기 왕복(경과보고·합계·설정의 연락처·계좌가 들어가는지), 영수증 첨부 docx
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

from core.paths import DATA_DIR  # noqa: E402
from core.report import load_settings  # noqa: E402
from core.secrets import SECRETS_TOML, load_secrets  # noqa: E402

KEYS = ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_REPO", "DATA_BRANCH", "APP_ENV")
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
    """필수: API 키·APP_ENV. GITHUB_TOKEN이 있으면(클라우드) GITHUB_REPO·DATA_BRANCH도."""
    required = ["ANTHROPIC_API_KEY", "APP_ENV"]
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
        notes.append("⚠️ 공개 저장소 — 계좌·연락처·교사명단이 공개됨. 비공개로 바꾸세요")
    ws = GitHubStorage(repo, branch).checkout()
    settings = load_settings(ws.path)
    record("GitHub 읽기", not notes,
           f"{repo.full_name}@{branch}: 파일 {len(ws.content)}개, settings.json 읽음({settings.현재연도} 회계연도)"
           + (" / " + " / ".join(notes) if notes else ""))
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


@step("설정")
def settings_check(data_dir: Path) -> None:
    s = load_settings(data_dir)
    fields = (("기안자 연락처", s.연락처.기안자), ("기안단체장 연락처", s.연락처.기안단체장), ("은행명", s.계좌.은행명),
              ("계좌번호", s.계좌.계좌번호), ("예금주", s.계좌.예금주))
    empty = [n for n, v in fields if not v]
    record("설정", True, f"settings.json 읽음({s.현재연도} 회계연도). "
           + (f"비어 있는 칸: {', '.join(empty)} — 문서에서도 빈칸" if empty else "연락처·계좌 모두 채워짐"))


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


@step("보고서 왕복")
def report_round_trip(data_dir: Path) -> None:
    from core.models import Receipt
    from core.report import build_report
    from hwpx.fill import render
    from hwpx.reader import read_document
    from hwpx.receipts import build_receipts_docx
    from PIL import Image
    s = load_settings(data_dir)
    body = "점검용 경과보고입니다.\n10월 1일 점검 : 3,000원"
    doc = s.fill_contacts(build_report(s, body, 3_000))
    with tempfile.TemporaryDirectory() as d:
        res = read_document(render(doc, Path(d) / "check.hwpx"))
        img = Path(d) / "r.png"
        Image.new("RGB", (60, 80), "white").save(img)
        docx = build_receipts_docx("", "", [Receipt(순번=1, 이미지=str(img), 금액=3_000)], Path(d) / "r.docx").stat().st_size
    back = res.doc
    keys = ("경과보고", "합계B", "기안자", "주관단체", "기안자연락처", "은행명", "계좌번호", "예금주")
    ok = all(getattr(back, k) == getattr(doc, k) for k in keys) and back.본당보조 is None
    record("보고서 왕복", ok and docx > 0,
           f"경과보고·합계 {doc.합계B:,}원·설정값(연락처·계좌 포함) 생성·읽기 {'일치' if ok else '불일치'}, "
           f"영수증 docx {docx:,}바이트")


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
    settings_check(data_dir)
    if a.skip_claude:
        record("Claude API", None, "--skip-claude")
    else:
        claude(s, data_dir)
    report_round_trip(data_dir)
    print(table())
    return 1 if any(r[1] == "실패" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
