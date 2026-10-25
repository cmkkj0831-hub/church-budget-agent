"""⚙️ 설정: 기본 정보·기안자·연락처·계좌·교사명단, Claude 모델, API 키 상태(모두 data/settings.json).
보고서에는 주관단체·기안자·기안단체장·기본 장소·참가대상·연락처·계좌가 들어간다."""
import forms
import lib
import streamlit as st

from core.models import Settings

s = lib.settings()
st.title("⚙️ 설정")

tab_basic, tab_ai = st.tabs(["기본 정보·연락처", "Claude 모델·API 키"])

with tab_basic:
    data = forms.basic_form(s, "set")
    if data:
        if lib.save_settings("설정: 기본 정보·연락처 수정", Settings(**data)):
            st.success("저장했습니다.")

with tab_ai:
    with st.form("ai"):
        c = st.columns(2)
        모델 = c[0].text_input("Claude 모델 ID", s.모델)
        fallbacks = c[1].selectbox("거절 시 대체 모델", ["off", "default"], index=["off", "default"].index(s.llm_fallbacks),
                                   format_func={"off": "끔", "default": "서버 기본 대체(beta)"}.get)
        if st.form_submit_button("저장", type="primary"):
            data = s.model_dump()
            data.update(모델=모델.strip(), llm_fallbacks=fallbacks)
            if lib.save_settings("설정: Claude 모델 수정", Settings(**data)):
                st.success("저장했습니다.")
    st.divider()
    forms.api_key_status()
