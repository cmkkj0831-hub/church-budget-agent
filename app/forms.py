"""설정 화면과 회계연도 전환 마법사가 함께 쓰는 입력 묶음."""
from pathlib import Path

import lib
import streamlit as st

from core.models import Settings
from core.usage import load_usage


def basic_form(s: Settings, key: str) -> dict | None:
    """기본 정보·기안자·연락처·계좌·교사명단. 저장 버튼을 누르면 settings dict 반환."""
    with st.form(f"{key}_basic"):
        st.markdown("**기본 정보**")
        b = s.기본정보
        c = st.columns(3)
        본당명 = c[0].text_input("본당명", b.본당명)
        분과 = c[1].text_input("분과", b.분과)
        주관단체 = c[2].text_input("주관단체", b.주관단체)
        c = st.columns(3)
        분과장 = c[0].text_input("분과장", b.분과장)
        기본장소 = c[1].text_input("기본 장소", b.기본장소)
        참가대상 = c[2].text_input("기본 참가대상", b.참가대상)
        st.markdown("**기안자**")
        c = st.columns(2)
        기안자 = c[0].text_input("기안자(이름 세례명)", s.기안정보.기안자)
        기안단체장 = c[1].text_input("기안단체장(이름 세례명)", s.기안정보.기안단체장)

        st.markdown("**연락처·계좌** (신청서·보고서에 자동으로 들어갑니다. 비워 두면 문서에서도 빈칸)")
        c = st.columns(2)
        연1 = c[0].text_input("기안자 연락처", s.연락처.기안자)
        연2 = c[1].text_input("기안단체장 연락처", s.연락처.기안단체장)
        c = st.columns(3)
        은행 = c[0].text_input("은행명", s.계좌.은행명)
        계좌 = c[1].text_input("계좌번호", s.계좌.계좌번호)
        예금주 = c[2].text_input("예금주", s.계좌.예금주)

        st.markdown("**교사명단** (한 줄에 '이름 세례명')")
        명단 = st.text_area("교사명단", "\n".join(s.교사명단), height=180, label_visibility="collapsed")
        saved = st.form_submit_button("저장", type="primary")
    if not saved:
        return None
    data = s.model_dump()
    data["기본정보"].update(본당명=본당명, 분과=분과, 주관단체=주관단체, 분과장=분과장, 기본장소=기본장소, 참가대상=참가대상)
    data["기안정보"].update(기안자=기안자, 기안단체장=기안단체장)
    data["연락처"] = {"기안자": 연1.strip(), "기안단체장": 연2.strip()}
    data["계좌"] = {"은행명": 은행.strip(), "계좌번호": 계좌.strip(), "예금주": 예금주.strip()}
    data["교사명단"] = [x.strip() for x in 명단.splitlines() if x.strip()]
    return data


def api_key_status() -> None:
    """API 키는 앱에서 입력·저장하지 않는다. 상태만 보여 준다."""
    key = lib.secrets().get("ANTHROPIC_API_KEY", "")
    rows = load_usage(Path(st.session_state.ws.path) / str(lib.ledger().year) / "usage.csv")
    last = rows[-1]["시각"] if rows else "기록 없음"
    c = st.columns(3)
    c[0].metric("Claude API 키", "설정됨" if key else "없음")
    c[1].metric("끝 4자리", key[-4:] if key else "-")
    c[2].metric("마지막 호출 성공", last)
    st.caption("키 교체는 개발자에게 안전하게 전달하세요(메신저 평문 대신 직접 전달 등). "
               "개발자가 Streamlit Cloud Secrets에서 바꿉니다. 이 앱에서는 키를 입력·저장하지 않습니다.")
