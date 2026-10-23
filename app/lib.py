"""앱 공통: 로그인·권한(ADMIN/VIEWER), 저장소·세션 상태(연도별), 반영(이력 기록 + 커밋), 되돌리기,
민감 설정(암호화), LLM 사용량 기록, 문서 생성, 다운로드 안내, 장부 변화 요약, 상단 배지."""
import io
import sys
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from agents.llm import set_usage_hook  # noqa: E402
from core.changes import record_unit, snapshot, undo_unit  # noqa: E402
from core.issues import IssueBook, cumulative_reconciliation, issue_color  # noqa: E402
from core.ledger import Ledger, load_settings, read_csv, save_settings  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402
from core.private import PrivateError, PrivateSettings, check_admin, load_private, save_private  # noqa: E402
from core.secrets import apply_secrets  # noqa: E402
from core.storage import ConflictError, storage_from_secrets  # noqa: E402
from core.usage import append_usage, usage_row  # noqa: E402

ADMIN, VIEWER = "ADMIN", "VIEWER"
HANDLER = "회계담당"
DOC_ERROR_TYPES = ("계산 불일치", "누계 누락", "기재 오류", "문서 누락", "번호 중복")


# ---------------------------------------------------------------- secrets·로그인

def secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except Exception:  # secrets.toml이 없을 때
        return {}


def app_env() -> str:
    return secrets().get("APP_ENV", "dev")


def role() -> str | None:
    return st.session_state.get("role")


def is_admin() -> bool:
    return role() == ADMIN


def login() -> None:
    s = secrets()
    load()
    private = st.session_state.private
    if not (s.get("ADMIN_PASSWORD") or private.관리자비밀번호해시):
        st.error("Streamlit secrets에 ADMIN_PASSWORD(최초 접속용)를 설정하세요.")
        st.stop()
    st.title("성당 초등부 교사회 예산 관리")
    with st.form("login"):
        pw = st.text_input("비밀번호", type="password")
        if s.get("VIEWER_PASSWORD"):
            st.caption("열람용 비밀번호로 로그인하면 대시보드만 볼 수 있습니다.")
        if st.form_submit_button("로그인", type="primary"):
            how = check_admin(pw, private, s.get("ADMIN_PASSWORD", ""))
            if how:
                st.session_state.role = ADMIN
                st.session_state.recovery = how == "secret" and bool(private.관리자비밀번호해시)
                st.session_state.first_login = how == "secret" and not private.관리자비밀번호해시
            elif s.get("VIEWER_PASSWORD") and pw == s["VIEWER_PASSWORD"]:
                st.session_state.role = VIEWER
            else:
                st.error("비밀번호가 맞지 않습니다.")
                st.stop()
            st.rerun()
    st.stop()


def require_admin() -> None:
    if not is_admin():
        st.warning("회계담당(관리자)만 쓸 수 있는 화면입니다.")
        st.stop()


def require_writable() -> None:
    """마감된(지난) 연도면 쓰기 화면을 막는다."""
    if ledger().read_only:
        st.info(f"{ledger().year}년은 마감된 연도라 읽기 전용입니다. 대시보드에서 보거나 엑셀로 내려받으세요.")
        st.stop()


# ---------------------------------------------------------------- 저장소·상태

def storage():
    if "_storage" not in st.session_state:
        st.session_state._storage = storage_from_secrets(secrets(), DATA_DIR)
    return st.session_state._storage


def settings_key() -> str:
    return secrets().get("SETTINGS_KEY", "")


def load(force: bool = False, year: int | None = None) -> None:
    ss = st.session_state
    if force or "ws" not in ss:
        ss.ws = storage().checkout()
    if force or "ledger" not in ss or year is not None:
        settings = load_settings(ss.ws.path)
        y = year or ss.get("year") or settings.현재연도
        if str(y) not in settings.연도별:
            y = settings.현재연도
        ss.year = y
        ss.ledger = Ledger.load(ss.ws.path, y)
        ss.book = IssueBook.load(ss.ws.path, y)
        info = ss.ws.path / str(y) / "plan_info.csv"
        ss.plan_info = read_csv(info) if info.exists() else []
        ss.base = snapshot(ss.ledger, ss.book)
        try:
            ss.private = load_private(settings_key(), ss.ws.path / "settings_private.enc")
            ss.private_error = ""
        except PrivateError as e:
            ss.private, ss.private_error = PrivateSettings(), str(e)
    set_usage_hook(_record_usage)


def needs_setup() -> bool:
    """첫 접속: 민감 설정 파일이 없거나 비어 있으면(비밀번호·연락처·계좌 모두 없음) 설정부터 하도록 안내."""
    load()
    ss = st.session_state
    return bool(ss.private_error) or not (ss.ws.path / "settings_private.enc").exists() \
        or ss.private == PrivateSettings()


def ledger() -> Ledger:
    load()
    return st.session_state.ledger


def book() -> IssueBook:
    load()
    return st.session_state.book


def plan_info() -> list[dict]:
    load()
    return st.session_state.plan_info


def private() -> PrivateSettings:
    load()
    return st.session_state.private


def _commit(message: str) -> list[str] | None:
    try:
        changed = storage().commit(st.session_state.ws, message)
    except ConflictError as e:
        st.error(f"저장하지 못했습니다: {e}")
        if st.button("새로 불러오기"):
            load(force=True)
            st.rerun()
        return None
    return changed


def persist(message: str, 이슈ID: str = "") -> str | None:
    """반영: 이력(반영 단위) 기록 → 작업 사본 저장 → 원본(로컬/GitHub)에 커밋. 반영 이력ID 또는 None."""
    ss = st.session_state
    unit = record_unit(ss.ledger, ss.book, ss.base, message, HANDLER, 이슈ID)
    ss.ledger.save(ss.ws.path)
    ss.book.save(ss.ws.path)
    if _commit(f"{message} [{unit}]" if unit else message) is None:
        return None
    load(force=True)
    return unit or ""


def undo(unit: str) -> str | None:
    ss = st.session_state
    new = undo_unit(ss.ledger, ss.book, unit, HANDLER)
    ss.ledger.save(ss.ws.path)
    ss.book.save(ss.ws.path)
    if _commit(f"되돌리기: {unit} [{new}]") is None:
        return None
    load(force=True)
    return new


def save_settings_and_private(message: str, settings=None, private_settings=None) -> bool:
    """설정(settings.json)과 민감 설정(settings_private.enc, 암호화)을 저장·커밋."""
    ss = st.session_state
    if private_settings is not None and ss.get("private_error"):
        # 키가 틀려 못 읽은 상태에서 저장하면 기존 암호화 설정을 빈 값으로 덮어쓴다
        st.error("민감 설정을 읽지 못해 저장하지 않았습니다. secrets의 SETTINGS_KEY를 확인하세요.")
        return False
    if settings is not None:
        save_settings(settings, ss.ws.path)
    if private_settings is not None:
        save_private(private_settings, settings_key(), ss.ws.path / "settings_private.enc")
    if _commit(message) is None:
        return False
    load(force=True)
    return True


def reload_button() -> None:
    with st.sidebar:
        st.caption(f"저장소: {storage().describe()} · 환경 {app_env()}")
        if st.button("↻ 새로 불러오기", width="stretch"):
            load(force=True)
            st.rerun()


# ---------------------------------------------------------------- LLM 사용량

def _record_usage(info) -> None:
    """모든 LLM 호출을 data/{연도}/usage.csv에 쓰고 바로 커밋(충돌이면 다음 반영에 함께 올라간다)."""
    ss = st.session_state
    if "ws" not in ss:
        return
    year = ss.get("year") or load_settings(ss.ws.path).현재연도
    append_usage(ss.ws.path / str(year) / "usage.csv", [usage_row(info, app_env())])
    try:
        storage().commit(ss.ws, f"사용량: {info.tool}")
    except ConflictError:
        pass


def llm_client():
    """테스트는 session_state['_llm_client']에 대역을 넣는다. 없으면 에이전트가 직접 만든다."""
    return st.session_state.get("_llm_client")


# ---------------------------------------------------------------- 문서

def render_hwpx(doc) -> bytes:
    from hwpx.fill import render
    with tempfile.TemporaryDirectory() as d:
        filled = apply_secrets(doc, private().secrets_view())
        return render(filled, Path(d) / "doc.hwpx", ledger().settings.hwpx_row_mode).read_bytes()


def receipts_docx(문서번호: str, 행사명: str, receipts) -> bytes:
    from hwpx.receipts import build_receipts_docx
    with tempfile.TemporaryDirectory() as d:
        return build_receipts_docx(문서번호, 행사명, receipts, Path(d) / "r.docx").read_bytes()


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def save_uploads(key: str) -> list[Path]:
    """session_state[key]의 (파일명, 바이트) 목록을 임시 폴더에 저장. 업로드 위젯과 테스트가 같은 경로를 쓴다."""
    files = st.session_state.get(key) or []
    folder = Path(tempfile.mkdtemp(prefix="uploads_"))
    paths = []
    for name, data in files:
        p = folder / Path(name).name
        p.write_bytes(data)
        paths.append(p)
    return paths


def uploader(label: str, key: str, types: list[str]) -> None:
    files = st.file_uploader(label, type=types, accept_multiple_files=True, key=f"{key}_widget")
    if files:
        st.session_state[key] = [(f.name, f.getvalue()) for f in files]
    if st.session_state.get(key):
        st.caption(f"{len(st.session_state[key])}개: " + ", ".join(n for n, _ in st.session_state[key]))


def account_override(prefix: str):
    """문서별 계좌 덮어쓰기 입력(예금주가 다를 때). 비우면 설정값."""
    from core.models import AccountInfo
    with st.expander("이 문서만 다른 계좌 쓰기(예금주가 다를 때)"):
        st.caption("비워 두면 설정의 기본 계좌를 씁니다. 입력값은 문서에만 들어가고 장부·저장소에 남지 않습니다.")
        a, b, c = st.columns(3)
        a.text_input("은행명", key=f"{prefix}_bank")
        b.text_input("계좌번호", key=f"{prefix}_account")
        c.text_input("예금주", key=f"{prefix}_holder")
    ss = st.session_state
    return AccountInfo(은행명=ss.get(f"{prefix}_bank", ""), 계좌번호=ss.get(f"{prefix}_account", ""),
                       예금주=ss.get(f"{prefix}_holder", ""))


# ---------------------------------------------------------------- 표시

def won(n) -> str:
    return "" if n is None else f"{n:,}원"


def download_guidance(month_label: str, filename: str) -> None:
    st.info(f"📁 **{ledger().year}/{month_label}** 폴더에 저장하세요. 권장 파일명: `{filename}`")


def snapshot_view() -> dict:
    L, B = ledger(), book()
    rec = cumulative_reconciliation(L, B)
    return {"다음문서번호": L.next_doc_number(), "재계산누계": rec.summary.재계산 if L.rows else 0,
            "잔여": L.remaining(), "열린이슈": {i.이슈ID for i in B.open_issues()}, "행수": len(L.rows)}


def show_changes(before: dict, unit: str | None = None, title: str = "장부 변화") -> None:
    after = snapshot_view()
    st.subheader(title)
    c = st.columns(4)
    c[0].metric("다음 문서번호", f"{after['다음문서번호']:03d}", after["다음문서번호"] - before["다음문서번호"])
    c[1].metric("신청누계(재계산)", won(after["재계산누계"]), f"{after['재계산누계'] - before['재계산누계']:+,}원")
    c[2].metric("잔여", won(after["잔여"]), f"{after['잔여'] - before['잔여']:+,}원")
    c[3].metric("장부 행", after["행수"], after["행수"] - before["행수"])
    closed = sorted(before["열린이슈"] - after["열린이슈"])
    opened = sorted(after["열린이슈"] - before["열린이슈"])
    if closed:
        st.success("해결된 이슈: " + ", ".join(closed))
    if opened:
        st.warning("새로 등록된 이슈: " + ", ".join(opened))
    if unit:
        st.caption(f"이 반영은 이력 {unit}로 기록됐습니다. 잘못 반영했다면 오류 관리 › 이력에서 되돌릴 수 있습니다.")


def findings_box(findings) -> tuple[list, list]:
    errors = [f for f in findings if f.수준 == "오류"]
    warnings = [f for f in findings if f.수준 == "경고"]
    for f in errors:
        st.error(f"⛔ [{f.유형}] {f.설명}")
    for f in warnings:
        st.warning(f"⚠️ [{f.유형}] {f.설명}")
    if not findings:
        st.success("검증 통과")
    return errors, warnings


def topbar() -> None:
    """로그인 후 상단: 현재 회계연도 + 미해결 이슈 배지(문서 오류 빨강, 예산 경고 주황, 증빙 누락·미확인 회색)."""
    L, B = ledger(), book()
    open_issues = B.open_issues()
    groups = [("문서 오류", [i for i in open_issues if i.유형 in DOC_ERROR_TYPES], "C00000"),
              ("예산 경고", [i for i in open_issues if i.유형 == "예산 경고"], "C65911"),
              ("증빙 누락·미확인", [i for i in open_issues if i.유형 in ("증빙 누락", "미확인")], "7F7F7F")]
    badges = " ".join(
        f"<span style='background:#{c};color:white;border-radius:10px;padding:2px 10px;margin-right:4px'>"
        f"{label} {len(items)}</span>" for label, items, c in groups if items)
    state = " · 🔒 읽기 전용" if L.read_only else ""
    st.markdown(f"**{L.year} 회계연도**{state} &nbsp; {badges or '미해결 이슈 없음'}", unsafe_allow_html=True)
    if st.session_state.get("recovery") or st.session_state.get("first_login"):
        st.warning("비상용(또는 최초) 비밀번호로 로그인했습니다. 설정에서 관리자 비밀번호를 새로 정하세요.")


def unreported_requests():
    return [r for r in ledger().requests() if not r.연결문서번호 and not r.취소]


def today() -> date:
    return st.session_state.get("_today") or date.today()


def now() -> datetime:
    return datetime.now()


__all__ = ["issue_color"]
