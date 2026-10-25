"""⚙️ 설정: 기본 정보·기안자·연락처·계좌·교사명단, 문서 양식, Claude 모델, API 키 상태(모두 data/settings.json)."""
import forms
import lib
import streamlit as st

from core.models import Settings

s = lib.settings()
st.title("⚙️ 설정")

tab_basic, tab_rules, tab_ai = st.tabs(["기본 정보·연락처", "문서 양식", "Claude 모델·API 키"])

with tab_basic:
    data = forms.basic_form(s, "set")
    if data:
        if lib.save_settings("설정: 기본 정보·연락처 수정", Settings(**data)):
            st.success("저장했습니다.")

with tab_rules:
    with st.form("rules"):
        r = s.금액문서기준
        c = st.columns(3)
        보고서번호접두 = c[0].text_input("보고서 번호 앞부분", r.보고서번호접두,
                                  help="{연도}는 회계연도로 바뀝니다. 보고서 화면 문서번호 칸에 미리 들어갑니다.")
        시작 = c[1].text_input("기본 시작 시각", r.기본시작시각, help="행사 정보의 시각을 비웠을 때")
        종료 = c[2].text_input("기본 종료 시각", r.기본종료시각)
        row_mode = st.selectbox("세부내역 7행 초과 시", ["clone", "group"], index=["clone", "group"].index(s.hwpx_row_mode),
                                format_func={"clone": "행 복제(표가 길어짐)", "group": "계정과목별로 묶기"}.get)
        if st.form_submit_button("저장", type="primary"):
            data = s.model_dump()
            data["금액문서기준"].update(보고서번호접두=보고서번호접두.strip(), 기본시작시각=시작.strip(), 기본종료시각=종료.strip())
            data["hwpx_row_mode"] = row_mode
            if lib.save_settings("설정: 문서 양식 수정", Settings(**data)):
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
