"""진입점: streamlit run app/Home.py

앱 안에는 로그인이 없다. 들어오면 모든 기능을 쓴다. 접근 제한은 Streamlit Cloud 앱 공유 설정('초대한 사람만')으로 한다.
"""
import lib
import streamlit as st

st.set_page_config(page_title="초등부 교사회 예산", page_icon="⛪", layout="wide")

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
lib.load()
lib.reload_button()
lib.topbar()
st.navigation(list(pages.values())).run()
