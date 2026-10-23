"""설정 화면과 회계연도 전환 마법사가 함께 쓰는 입력 묶음."""
from pathlib import Path

import lib
import streamlit as st

from core.models import Settings
from core.private import PrivateError, PrivateSettings, change_admin_password, mask
from core.usage import load_usage


def basic_form(s: Settings, private: PrivateSettings, key: str) -> tuple[dict, PrivateSettings] | None:
    """기본 정보·기안자·연락처·계좌·교사명단. 저장 버튼을 누르면 (settings dict, private) 반환."""
    # 폼 안의 위젯은 제출 전까지 화면을 다시 그리지 않으므로 [보기]는 폼 밖에 둔다
    show = st.toggle("연락처·계좌 보기", key=f"{key}_show", help="누르면 가려진 값이 보이고 고칠 수 있습니다.")
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

        st.markdown("**연락처·계좌** (암호화해 저장합니다)")
        if show:
            c = st.columns(2)
            연1 = c[0].text_input("기안자 연락처", private.연락처.기안자)
            연2 = c[1].text_input("기안단체장 연락처", private.연락처.기안단체장)
            c = st.columns(3)
            은행 = c[0].text_input("은행명", private.계좌.은행명)
            계좌 = c[1].text_input("계좌번호", private.계좌.계좌번호)
            예금주 = c[2].text_input("예금주", private.계좌.예금주)
        else:
            st.caption(f"기안자 연락처 {mask(private.연락처.기안자) or '(없음)'} · 기안단체장 연락처 "
                       f"{mask(private.연락처.기안단체장) or '(없음)'} · {private.계좌.은행명 or '은행 없음'} "
                       f"{mask(private.계좌.계좌번호) or '(계좌 없음)'} · 예금주 {mask(private.계좌.예금주, 1) or '(없음)'}")
            연1, 연2 = private.연락처.기안자, private.연락처.기안단체장
            은행, 계좌, 예금주 = private.계좌.은행명, private.계좌.계좌번호, private.계좌.예금주

        st.markdown("**교사명단** (한 줄에 '이름 세례명')")
        명단 = st.text_area("교사명단", "\n".join(s.교사명단), height=180, label_visibility="collapsed")
        saved = st.form_submit_button("저장", type="primary")
    if not saved:
        return None
    data = s.model_dump()
    data["기본정보"].update(본당명=본당명, 분과=분과, 주관단체=주관단체, 분과장=분과장, 기본장소=기본장소, 참가대상=참가대상)
    data["기안정보"].update(기안자=기안자, 기안단체장=기안단체장)
    data["교사명단"] = [x.strip() for x in 명단.splitlines() if x.strip()]
    new_private = private.model_copy(deep=True)
    new_private.연락처.기안자, new_private.연락처.기안단체장 = 연1.strip(), 연2.strip()
    new_private.계좌.은행명, new_private.계좌.계좌번호, new_private.계좌.예금주 = 은행.strip(), 계좌.strip(), 예금주.strip()
    return data, new_private


def password_form(key: str) -> None:
    """관리자 비밀번호 변경(현재 비밀번호 확인 후). 암호화 저장."""
    with st.form(f"{key}_pw"):
        st.markdown("**관리자 비밀번호 변경**")
        cur = st.text_input("현재 비밀번호", type="password")
        new = st.text_input("새 비밀번호(8자 이상)", type="password")
        again = st.text_input("새 비밀번호 확인", type="password")
        if st.form_submit_button("비밀번호 변경"):
            if new != again:
                st.error("새 비밀번호가 서로 다릅니다.")
                return
            p = lib.private().model_copy(deep=True)
            try:
                change_admin_password(p, cur, new, lib.secrets().get("ADMIN_PASSWORD", ""))
            except PrivateError as e:
                st.error(str(e))
                return
            if lib.save_settings_and_private("설정: 관리자 비밀번호 변경", private_settings=p):
                st.session_state.recovery = st.session_state.first_login = False
                st.success("비밀번호를 바꿨습니다. 다음 로그인부터 새 비밀번호를 쓰세요.")


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
