"""진입점: streamlit run app/Home.py

화면은 집행 보고서와 설정 두 개. 앱 안에는 로그인이 없고, 접근 제한은 Streamlit Cloud 앱 공유 설정('초대한 사람만')으로 한다.
"""
import lib
import streamlit as st

st.set_page_config(page_title="초등부 교사회 집행보고서", page_icon="⛪", layout="wide")

pages = [st.Page("views/report.py", title="집행 보고서", icon="🧾", default=True),
         st.Page("views/settings.py", title="설정", icon="⚙️")]
lib.load()
lib.reload_button()
st.navigation(pages).run()
