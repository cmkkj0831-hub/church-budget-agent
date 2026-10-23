"""⚙️ 설정(회계담당): 기본 정보·기안자·연락처·계좌(암호화)·교사명단, 금액 기준, 문서 양식, Claude 모델·토큰 단가,
관리자 비밀번호, API 키 상태."""
import forms
import lib
import streamlit as st

from core.models import Settings

lib.require_admin()
L = lib.ledger()
s = L.settings
st.title("⚙️ 설정")
if st.session_state.get("private_error"):
    st.error(f"민감 설정을 읽지 못했습니다: {st.session_state.private_error}")
elif lib.needs_setup():
    st.info("처음 접속하셨습니다. 아래 순서로 설정해 주세요.\n\n"
            "1. **관리자** 탭에서 내 비밀번호로 바꾸기\n"
            "2. **기본 정보·연락처** 탭에서 [연락처·계좌 보기]를 켜고 연락처·계좌를 넣은 뒤 저장\n"
            "3. 왼쪽 **인수인계**에서 체크리스트 확인")

tab_basic, tab_rules, tab_ai, tab_admin = st.tabs(["기본 정보·연락처", "금액·문서 양식", "Claude 모델·사용량 단가", "관리자"])

with tab_basic:
    out = forms.basic_form(s, lib.private(), "set")
    if out:
        data, private = out
        if lib.save_settings_and_private("설정: 기본 정보·연락처 수정", Settings(**data), private):
            st.success("저장했습니다.")

with tab_rules:
    with st.form("rules"):
        r = s.금액문서기준
        st.markdown(f"**금액 기준** ({L.year}년)")
        c = st.columns(3)
        배정액 = c[0].number_input("예산 배정액", 0, value=L.allocation, step=10000, disabled=L.read_only)
        운영비단가 = c[1].number_input("교사회 운영비 단가(1인)", 0, value=r.운영비단가, step=1000)
        교사수 = c[2].number_input("교사 수", 0, value=r.교사수)
        st.markdown("**문서 양식**")
        c = st.columns(3)
        신청서번호접두 = c[0].text_input("신청서 번호 접두", r.신청서번호접두, help="{연도}는 회계연도로 바뀝니다")
        보고서번호접두 = c[1].text_input("보고서 번호 접두", r.보고서번호접두)
        다음 = c[2].number_input("다음 문서번호(최소)", 1, value=L.year_info.다음문서번호,
                               help=f"장부 기준 다음 번호는 {L.next_doc_number()}. 이보다 작게 넣어도 장부 번호 다음부터 씁니다.")
        c = st.columns(3)
        시작 = c[0].text_input("기본 시작 시각", r.기본시작시각)
        종료 = c[1].text_input("기본 종료 시각", r.기본종료시각)
        결재란 = c[2].text_input("결재란(쉼표로 구분)", ", ".join(r.결재란))
        row_mode = st.selectbox("세부내역 7행 초과 시", ["clone", "group"], index=["clone", "group"].index(s.hwpx_row_mode),
                                format_func={"clone": "행 복제(표가 길어짐)", "group": "계정과목별로 묶기"}.get)
        if st.form_submit_button("저장", type="primary"):
            data = s.model_dump()
            data["금액문서기준"].update(운영비단가=int(운영비단가), 교사수=int(교사수), 신청서번호접두=신청서번호접두,
                                   보고서번호접두=보고서번호접두, 기본시작시각=시작, 기본종료시각=종료,
                                   결재란=[x.strip() for x in 결재란.split(",") if x.strip()])
            data["hwpx_row_mode"] = row_mode
            data["연도별"][str(L.year)].update(예산배정액=int(배정액), 다음문서번호=int(다음))
            if lib.save_settings_and_private("설정: 금액·문서 양식 수정", Settings(**data)):
                st.success("저장했습니다.")

with tab_ai:
    with st.form("ai"):
        c = st.columns(2)
        모델 = c[0].text_input("Claude 모델 ID", s.모델)
        fallbacks = c[1].selectbox("거절 시 대체 모델", ["off", "default"], index=["off", "default"].index(s.llm_fallbacks),
                                   format_func={"off": "끔", "default": "서버 기본 대체(beta)"}.get)
        st.markdown("**토큰 단가·환율** (사용량 탭의 예상 비용 계산용 — Anthropic 요금표를 보고 입력)")
        p = s.사용량단가
        c = st.columns(3)
        입력 = c[0].number_input("입력 단가(USD / 100만 토큰)", 0.0, value=float(p.입력_USD_1M), step=0.1)
        출력 = c[1].number_input("출력 단가(USD / 100만 토큰)", 0.0, value=float(p.출력_USD_1M), step=0.1)
        환율 = c[2].number_input("환율(원 / USD)", 0.0, value=float(p.환율_KRW_USD), step=10.0)
        if st.form_submit_button("저장", type="primary"):
            data = s.model_dump()
            data.update(모델=모델.strip(), llm_fallbacks=fallbacks,
                        사용량단가={"입력_USD_1M": 입력, "출력_USD_1M": 출력, "환율_KRW_USD": 환율})
            if lib.save_settings_and_private("설정: 모델·사용량 단가 수정", Settings(**data)):
                st.success("저장했습니다.")

with tab_admin:
    forms.password_form("set")
    st.divider()
    forms.api_key_status()
