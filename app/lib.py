"""앱 공통: 저장소(설정 파일)·세션 상태, 설정 저장, 문서 생성(설정의 연락처·계좌 채움), 업로드,
행사 정보 입력(일시·장소·참가대상·인원), 문서별 계좌 덮어쓰기, 표시 도우미.
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

from core.models import AccountInfo, Settings  # noqa: E402
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


def account_override(prefix: str) -> AccountInfo:
    """문서별 계좌 덮어쓰기 입력(예금주가 다를 때). 비우면 설정값."""
    with st.expander("이 문서만 다른 계좌 쓰기(예금주가 다를 때)"):
        a = settings().계좌
        st.caption(f"비워 두면 설정의 기본 계좌를 씁니다(현재: {a.은행명 or '은행 없음'} {a.계좌번호 or '계좌 없음'} "
                   f"{a.예금주 or '예금주 없음'}). 입력값은 이 문서에만 들어가고 설정에는 남지 않습니다.")
        c1, c2, c3 = st.columns(3)
        c1.text_input("은행명", key=f"{prefix}_bank")
        c2.text_input("계좌번호", key=f"{prefix}_account")
        c3.text_input("예금주", key=f"{prefix}_holder")
    ss = st.session_state
    return AccountInfo(은행명=ss.get(f"{prefix}_bank", ""), 계좌번호=ss.get(f"{prefix}_account", ""),
                       예금주=ss.get(f"{prefix}_holder", ""))


# ---------------------------------------------------------------- 행사 정보(일시·장소·참가대상·인원)

EVENT_KEYS = ("시작일", "시작시각", "종료일", "종료시각", "인원_총", "인원_남", "인원_여", "장소", "참가대상")


def receipt_span(receipts) -> tuple[date | None, date | None]:
    days = sorted(r.날짜 for r in receipts if r.날짜)
    return (days[0], days[-1]) if days else (None, None)


def event_fields(prefix: str) -> None:
    """일시(시작·종료일, 시각)·장소·참가대상·인원(총·남·여) 입력.
    비워 둔 칸: 시각은 설정 기본 시각, 장소·참가대상은 설정 기본값으로 문서에 들어간다. 값은 event_values로 읽는다."""
    ss = st.session_state
    base = settings().기본정보
    for k in ("시작일", "시작시각", "종료일", "종료시각"):
        ss.setdefault(f"{prefix}_{k}", None)
    st.markdown("**행사 정보** (문서의 일시·장소·참가대상·인원 칸)")
    a, b, c, d = st.columns(4)
    a.date_input("시작일", key=f"{prefix}_시작일")
    b.time_input("시작 시각", key=f"{prefix}_시작시각")
    c.date_input("종료일", key=f"{prefix}_종료일")
    d.time_input("종료 시각", key=f"{prefix}_종료시각")
    a, b, c = st.columns(3)
    a.number_input("인원(총)", 0, key=f"{prefix}_인원_총")
    b.number_input("남", 0, key=f"{prefix}_인원_남")
    c.number_input("여", 0, key=f"{prefix}_인원_여")
    a, b = st.columns(2)
    a.text_input("장소", key=f"{prefix}_장소", placeholder=f"비우면 {base.기본장소}")
    b.text_input("참가대상", key=f"{prefix}_참가대상", placeholder=f"비우면 {base.참가대상}")


def event_entered(prefix: str) -> dict:
    """사용자가 채운 행사 정보 칸만 {칸: 값}."""
    ss = st.session_state
    out = {}
    for k in EVENT_KEYS:
        v = ss.get(f"{prefix}_{k}")
        if isinstance(v, str):
            v = v.strip()
        if v:
            out[k] = v
    return out


def fill_event_blanks(prefix: str, values: dict) -> None:
    """비어 있는 행사 정보 칸만 values로 채운다(위젯을 그리기 전에 부를 것)."""
    entered = event_entered(prefix)
    for k, v in values.items():
        if k in EVENT_KEYS and k not in entered and v not in (None, "", 0):
            st.session_state[f"{prefix}_{k}"] = v


def event_values(prefix: str) -> dict:
    """행사 정보 칸 → 문서 필드(DocBase). 시각을 비우면 설정 기본 시각, 종료일을 비우면 시작일."""
    rules = settings().금액문서기준
    e = event_entered(prefix)
    hm = lambda t: dtime(*map(int, t.split(":")))  # noqa: E731
    start_day = e.get("시작일")
    end_day = e.get("종료일") or start_day
    start = datetime.combine(start_day, e.get("시작시각") or hm(rules.기본시작시각)) if start_day else None
    end = datetime.combine(end_day, e.get("종료시각") or hm(rules.기본종료시각)) if end_day else None
    return {"시작": start, "종료": end, "장소": e.get("장소", ""), "참가대상": e.get("참가대상", ""),
            "인원_총": e.get("인원_총"), "인원_남": e.get("인원_남"), "인원_여": e.get("인원_여")}


# ---------------------------------------------------------------- 표시

def won(n) -> str:
    return "" if n is None else f"{n:,}원"
