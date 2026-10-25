"""앱 공통: 저장소(설정 파일)·세션 상태, 설정 저장, 문서 생성(설정의 연락처·계좌 채움), 업로드, 표시 도우미.
접근 제한은 코드가 아니라 Streamlit Cloud 앱 공유 설정('초대한 사람만')으로 한다."""
import io
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from core.models import Settings  # noqa: E402
from core.paths import DATA_DIR  # noqa: E402
from core.report import load_settings, save_settings as save_settings_file  # noqa: E402
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


def settings() -> Settings:
    load()
    return st.session_state.settings


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


# ---------------------------------------------------------------- 표시

def won(n) -> str:
    return "" if n is None else f"{n:,}원"
