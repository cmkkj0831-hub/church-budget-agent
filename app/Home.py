"""진입점: streamlit run app/Home.py

로그인: 회계담당(ADMIN) 비밀번호 = 앱에서 정한 비밀번호(암호화 저장) 또는 secrets의 ADMIN_PASSWORD(최초·비상 복구).
secrets에 VIEWER_PASSWORD가 있으면 열람용 로그인(대시보드 보기·엑셀 다운로드만)도 받는다.
"""
import lib
import streamlit as st

st.set_page_config(page_title="초등부 교사회 예산", page_icon="⛪", layout="wide")

if not lib.role():
    lib.login()

pages = {
    "request": st.Page("views/request.py", title="예산 신청서", icon="📝"),
    "report": st.Page("views/report.py", title="집행 보고서", icon="🧾"),
    "post": st.Page("views/post_settlement.py", title="사후 정산", icon="🔁"),
    "issues": st.Page("views/issues.py", title="오류 관리", icon="⚠️"),
    "dashboard": st.Page("views/dashboard.py", title="대시보드", icon="📊", default=True),
    "settings": st.Page("views/settings.py", title="설정", icon="⚙️"),
    "year": st.Page("views/year.py", title="회계연도 전환", icon="🗓️"),
    "handover": st.Page("views/handover.py", title="인수인계", icon="📋"),
}
st.session_state._pages = pages
visible = list(pages.values()) if lib.is_admin() else [pages["dashboard"]]

with st.sidebar:
    st.markdown(f"**{'회계담당(관리자)' if lib.is_admin() else '열람'}** 로그인")
    if st.button("로그아웃"):
        for k in list(st.session_state.keys()):
            if not k.startswith("_llm") and k != "_today":
                del st.session_state[k]
        st.rerun()
lib.load()
lib.reload_button()
lib.topbar()
st.navigation(visible).run()
