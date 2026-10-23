"""📋 인수인계(회계담당): 체크리스트(진행 상태 저장), 인수인계 패키지 zip, 사용설명서."""
import tempfile
from pathlib import Path

import lib
import streamlit as st

from core.export import export_plan_xlsx, export_xlsx
from core.ledger import load_settings
from core.models import AccountInfo, Contacts
from core.paths import ROOT

ss = st.session_state
ws = Path(ss.ws.path)
L, B = lib.ledger(), lib.book()
st.title("📋 인수인계")
MANUAL = ROOT / "docs" / "사용설명서.md"

tab_check, tab_pack, tab_manual = st.tabs(["체크리스트", "인수인계 패키지", "사용설명서"])

ITEMS = {
    "공유": "Streamlit 앱 공유 설정에서 새 담당자 이메일을 초대하고 이전 담당자를 제거했다(개발자에게 요청)",
    "API키": "Claude API 키 교체를 개발자에게 요청했다(새 담당자 이름으로 발급·전달)",
    "계좌": "연락처·계좌·예금주가 새 담당자 기준으로 맞다(설정 › 기본 정보·연락처)",
    "이슈": f"미해결 이슈 {len(B.open_issues())}건을 함께 검토했다(오류 관리)",
    "설명서": "사용설명서를 읽었다(이 화면 › 사용설명서)",
}

with tab_check:
    settings = load_settings(ws)
    done = dict(settings.진행상태.get("인수인계") or {})
    with st.form("handover"):
        new = {k: st.checkbox(text, value=bool(done.get(k))) for k, text in ITEMS.items()}
        if st.form_submit_button("저장", type="primary"):
            settings.진행상태["인수인계"] = new
            if lib.save_settings("인수인계: 체크리스트 갱신", settings):
                st.rerun()
    n = sum(bool(v) for v in done.values())
    st.progress(n / len(ITEMS), text=f"{n}/{len(ITEMS)} 완료")

with tab_pack:
    st.markdown("다음 담당자에게 넘길 자료를 한 파일로 받습니다. 설정의 연락처·계좌는 빼고 넣습니다.")
    if st.button("인수인계 패키지 만들기", type="primary"):
        files = {}
        with tempfile.TemporaryDirectory() as d:
            files[f"{L.year}_회계내역.xlsx"] = export_xlsx(L, B, Path(d) / "a.xlsx").read_bytes()
            files[f"{L.year}_사업계획서_집행현황.xlsx"] = export_plan_xlsx(
                L, lib.plan_info(), Path(d) / "b.xlsx", lib.today()).read_bytes()
        for name in ("issues.csv", "history.csv", "ledger.csv", "plan.csv", "usage.csv"):
            p = ws / str(L.year) / name
            if p.exists():
                files[f"{L.year}/{name}"] = p.read_bytes()
        shared = load_settings(ws).model_copy(update={"연락처": Contacts(), "계좌": AccountInfo()})
        files["settings(연락처·계좌 제외).json"] = shared.model_dump_json(indent=2).encode("utf-8")
        for doc in (MANUAL, MANUAL.with_name("회계담당_접속안내.md")):
            if doc.exists():
                files[doc.name] = doc.read_bytes()
        ss.handover_zip = lib.zip_bytes(files)
        ss.handover_files = list(files)
    if ss.get("handover_zip"):
        st.download_button("인수인계 패키지 zip", ss.handover_zip, file_name=f"{L.year}_인수인계.zip",
                           mime="application/zip", type="primary")
        st.caption("포함: " + ", ".join(ss.handover_files))

with tab_manual:
    st.markdown(MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else "사용설명서 파일이 없습니다.")
    if MANUAL.exists():
        st.download_button("사용설명서 내려받기(.md)", MANUAL.read_bytes(), file_name="사용설명서.md")
