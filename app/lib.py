"""앱 공통: 저장소(설정 파일)·세션 상태, 설정 저장, 문서 생성(설정의 연락처·계좌 채움), 업로드, 표시 도우미.
접근 제한은 코드가 아니라 Streamlit Cloud 앱 공유 설정('초대한 사람만')으로 한다."""
import io
import sys
import tempfile
import zipfile
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from core.models import Settings  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402
from core.report import load_plan, load_settings, nights_days  # noqa: E402
from core.report import save_settings as save_settings_file  # noqa: E402
from core.storage import ConflictError, storage_from_secrets  # noqa: E402


# ---------------------------------------------------------------- secrets

def secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except Exception:  # secrets.toml이 없을 때
        return {}


def app_env() -> str:
    return secrets().get("APP_ENV", "dev")


# ---------------------------------------------------------------- 저장소·설정

def storage():
    if "_storage" not in st.session_state:
        st.session_state._storage = storage_from_secrets(secrets(), DATA_DIR)
    return st.session_state._storage


def load(force: bool = False) -> None:
    ss = st.session_state
    if force or "ws" not in ss:
        ss.ws = storage().checkout()
    if force or "settings" not in ss:
        ss.settings = load_settings(ss.ws.path)
        ss.pop("plan", None)


def settings() -> Settings:
    load()
    return st.session_state.settings


def plan() -> list[dict]:
    """올해 사업계획(시기·사업명·세부내역). data/{회계연도}/plan.csv, 없으면 빈 목록."""
    load()
    ss = st.session_state
    if "plan" not in ss:
        ss.plan = load_plan(ss.ws.path, ss.settings.현재연도)
    return ss.plan


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


def save_settings(message: str, new: Settings) -> bool:
    """설정(settings.json, 연락처·계좌 포함)을 저장·커밋."""
    save_settings_file(new, st.session_state.ws.path)
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


def llm_client():
    """테스트는 session_state['_llm_client']에 대역을 넣는다. 없으면 에이전트가 직접 만든다."""
    return st.session_state.get("_llm_client")


def today() -> date:
    return st.session_state.get("_today") or date.today()


# ---------------------------------------------------------------- 문서·파일

def render_hwpx(doc) -> bytes:
    from hwpx.fill import render
    with tempfile.TemporaryDirectory() as d:
        filled = settings().fill_contacts(doc)
        return render(filled, Path(d) / "doc.hwpx", settings().hwpx_row_mode).read_bytes()


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


# ---------------------------------------------------------------- 일시·참가인원

def event_fields(prefix: str) -> None:
    """일시(시작·종료 날짜·시각, 박·일은 자동 계산해 보여 줌)와 참가인원(총, 남·여는 '기타' 안) 입력."""
    ss = st.session_state
    for k in ("시작일", "시작시각", "종료일", "종료시각"):
        ss.setdefault(f"{prefix}_{k}", None)
    rules = settings().금액문서기준
    st.markdown("**일시**")
    a, b, c, d = st.columns(4)
    a.date_input("시작일", key=f"{prefix}_시작일")
    b.time_input("시작 시각", key=f"{prefix}_시작시각", help=f"비우면 {rules.기본시작시각}")
    c.date_input("종료일", key=f"{prefix}_종료일", help="비우면 시작일과 같은 날")
    d.time_input("종료 시각", key=f"{prefix}_종료시각", help=f"비우면 {rules.기본종료시각}")
    start, end = ss.get(f"{prefix}_시작일"), ss.get(f"{prefix}_종료일")
    if start and end and end < start:
        st.warning("종료일이 시작일보다 앞섭니다.")
    nights, days = nights_days(start, end)
    if start:
        st.caption(f"→ {nights}박 {days}일 (일시로 자동 계산해 보고서에 넣습니다)")
    st.number_input("참가인원(총)", 0, key=f"{prefix}_인원_총")
    with st.expander("기타 (남·여 인원, 선택)"):
        a, b = st.columns(2)
        a.number_input("남", 0, key=f"{prefix}_인원_남")
        b.number_input("여", 0, key=f"{prefix}_인원_여")


def event_values(prefix: str) -> dict:
    """입력 → 보고서 필드(시작·종료·인원). 시각을 비우면 설정 기본 시각, 종료일을 비우면 시작일. 0명은 빈칸."""
    ss = st.session_state
    rules = settings().금액문서기준
    hm = lambda t: dtime(*map(int, t.split(":")))  # noqa: E731
    g = lambda k: ss.get(f"{prefix}_{k}")  # noqa: E731
    start_day, end_day = g("시작일"), g("종료일") or g("시작일")
    start = datetime.combine(start_day, g("시작시각") or hm(rules.기본시작시각)) if start_day else None
    end = datetime.combine(end_day, g("종료시각") or hm(rules.기본종료시각)) if end_day else None
    n = lambda k: int(g(k)) if g(k) else None  # noqa: E731
    return {"시작": start, "종료": end, "인원_총": n("인원_총"), "인원_남": n("인원_남"), "인원_여": n("인원_여")}


# ---------------------------------------------------------------- 표시

def won(n) -> str:
    return "" if n is None else f"{n:,}원"
