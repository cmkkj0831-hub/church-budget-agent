"""📝 예산 신청서: 자연어 → 초안(수정 가능, 계산값 실시간) → 빠진 정보 → 검증 → 미리보기 → 생성."""
from datetime import datetime, time

import lib
import pandas as pd
import streamlit as st

from agents.llm import AgentError
from agents.request_agent import EVENT_TYPES, RequestDraft, extract_request
from core.issues import commit_with_issues
from core.models import LineItem, RequestDoc

lib.require_writable()
L = lib.ledger()
st.title("📝 예산 신청서")

ITEM_COLS = ["계정과목", "내용", "단가", "수량"]


def show_result(result) -> None:
    row = result["row"]
    st.success(f"✅ {row.문서번호} {row.행사명} 신청서를 만들고 장부(순번 {row.순번})에 반영했습니다.")
    lib.download_guidance(row.월, row.파일명)
    st.download_button("hwpx 다운로드", result["bytes"], file_name=row.파일명, type="primary",
                       mime="application/octet-stream")
    if result["issues"]:
        st.warning("경고를 이슈로 등록했습니다: " + ", ".join(result["issues"]))
    lib.show_changes(result["before"], result.get("unit"))
    if st.button("새 신청서"):
        for k in [k for k in st.session_state.keys() if k.startswith("req_")]:
            del st.session_state[k]
        st.rerun()


if st.session_state.get("req_result"):  # 생성 뒤에는 결과만(바뀐 장부로 다시 계산한 초안을 보이지 않는다)
    show_result(st.session_state.req_result)
    st.stop()


def start(draft: RequestDraft) -> None:
    """초안을 위젯 상태에 옮긴다."""
    d, rules = draft.doc, L.settings.금액문서기준
    ss = st.session_state
    ss.req_draft = draft
    ss.req_result = None
    ss.req_행사명 = d.행사명
    ss.req_월 = d.월 or lib.today().month
    ss.req_사업ID = d.사업ID or next(iter(L.plan))
    ss.req_행사유형 = d.행사유형
    ss.req_목적 = d.목적
    start_dt = d.시작 or datetime.combine(lib.today(), time.fromisoformat(rules.기본시작시각))
    end_dt = d.종료 or datetime.combine(start_dt.date(), time.fromisoformat(rules.기본종료시각))
    ss.req_시작일, ss.req_시작시각 = start_dt.date(), start_dt.time()
    ss.req_종료일, ss.req_종료시각 = end_dt.date(), end_dt.time()
    ss.req_박, ss.req_일 = d.박 or 0, d.일 or 0
    ss.req_장소, ss.req_참가대상 = d.장소, d.참가대상
    ss.req_인원_총, ss.req_인원_남, ss.req_인원_여 = d.인원_총 or 0, d.인원_남 or 0, d.인원_여 or 0
    ss.req_행사일정 = d.행사일정
    ss.req_작성일 = lib.today()
    ss.req_items_df = pd.DataFrame([{k: getattr(i, k) for k in ITEM_COLS} for i in d.세부내역], columns=ITEM_COLS)
    ss.req_confirm = False


# ---------------------------------------------------------------- 1. 자연어 입력
with st.container(border=True):
    st.markdown("**1. 행사 설명**")
    text = st.text_area("어떤 행사인가요?", key="req_text", height=90,
                        placeholder="예: 12월 20일 성탄 행사, 어린이 120명, 간식 3000원씩, 선물 5000원씩")
    c1, c2 = st.columns([1, 1])
    if c1.button("초안 만들기", type="primary", disabled=not text.strip()):
        with st.spinner("Claude가 신청서 정보를 뽑는 중…"):
            try:
                start(extract_request(text, L, lib.today(), client=lib.llm_client()))
            except AgentError as e:
                st.error(f"초안을 만들지 못했습니다: {e}")
    if c2.button("빈 양식으로 직접 입력"):
        start(RequestDraft(RequestDoc(월=lib.today().month, 사업ID="", 행사명="")))

draft: RequestDraft | None = st.session_state.get("req_draft")
if draft is None:
    st.stop()
ss = st.session_state

# ---------------------------------------------------------------- 2. 초안 수정
with st.container(border=True):
    st.markdown("**2. 초안 확인·수정**")
    if draft.calls:
        st.caption(" · ".join(c.line() for c in draft.calls))
    if draft.missing:
        st.info("❓ 설명에 없던 정보입니다. 아래에서 채워 주세요: " + ", ".join(draft.missing))
    if draft.defaulted:
        st.caption("설정 기본값으로 채운 칸: " + ", ".join(draft.defaulted))

    plan_ids = list(L.plan)
    cand = [c["사업ID"] for c in draft.candidates]
    a, b, c = st.columns([3, 1, 2])
    a.text_input("행사명", key="req_행사명")
    b.number_input("대상 월", 1, 12, key="req_월")
    c.selectbox("사업", plan_ids, key="req_사업ID",
                format_func=lambda p: f"{p} {L.plan[p].사업명}" + (" ⭐" if p in cand else "")
                + f" (남은 한도 {L.business_limit(p):,}원)")
    if draft.candidates:
        st.caption("사업 후보: " + " / ".join(f"{c['사업ID']} {c['사업명']} — {c['근거']}" for c in draft.candidates))
    a, b = st.columns([1, 3])
    a.selectbox("행사유형", EVENT_TYPES, key="req_행사유형")
    b.text_input("목적", key="req_목적")
    a, b, c, d = st.columns(4)
    a.date_input("시작일", key="req_시작일")
    b.time_input("시작 시각", key="req_시작시각")
    c.date_input("종료일", key="req_종료일")
    d.time_input("종료 시각", key="req_종료시각")
    a, b, c, d, e, f = st.columns(6)
    a.number_input("박", 0, 30, key="req_박")
    b.number_input("일", 0, 31, key="req_일")
    c.number_input("인원(총)", 0, key="req_인원_총")
    d.number_input("남", 0, key="req_인원_남")
    e.number_input("여", 0, key="req_인원_여")
    f.date_input("작성일", key="req_작성일")
    a, b = st.columns(2)
    a.text_input("장소", key="req_장소")
    b.text_input("참가대상", key="req_참가대상")
    st.text_area("행사 일정", key="req_행사일정", height=110)
    account = lib.account_override("req")

    st.markdown("세부내역 (단가·수량을 고치면 금액·합계가 바로 다시 계산됩니다)")
    items_df = st.data_editor(
        ss.req_items_df, num_rows="dynamic", key="req_items", width="stretch",
        column_config={"단가": st.column_config.NumberColumn(min_value=0, step=100, format="%d"),
                       "수량": st.column_config.NumberColumn(min_value=0, step=1, format="%d")})

items = []
for row in items_df.to_dict("records"):
    if not str(row.get("내용") or "").strip() or pd.isna(row.get("단가")) or pd.isna(row.get("수량")):
        continue
    items.append(LineItem(계정과목=str(row.get("계정과목") or ""), 내용=str(row["내용"]),
                          단가=int(row["단가"]), 수량=int(row["수량"])))
n = lambda v: int(v) if v else None
doc = RequestDoc(
    월=int(ss.req_월), 사업ID=ss.req_사업ID, 작성일=ss.req_작성일, 행사명=ss.req_행사명.strip(),
    행사유형=ss.req_행사유형, 목적=ss.req_목적, 시작=datetime.combine(ss.req_시작일, ss.req_시작시각),
    종료=datetime.combine(ss.req_종료일, ss.req_종료시각), 박=n(ss.req_박), 일=n(ss.req_일),
    장소=ss.req_장소, 참가대상=ss.req_참가대상, 인원_총=n(ss.req_인원_총), 인원_남=n(ss.req_인원_남),
    인원_여=n(ss.req_인원_여), 행사일정=ss.req_행사일정, 세부내역=items, 계좌_override=account)
build = L.build_request(doc, today=lib.today())
built = build.docs[0]

# ---------------------------------------------------------------- 3. 계산·검증
with st.container(border=True):
    st.markdown("**3. 계산 결과와 검증**")
    calc = pd.DataFrame([{"내용": i.내용, "단가": i.단가, "수량": i.수량, "금액": i.금액} for i in built.세부내역])
    if not calc.empty:
        st.dataframe(calc, hide_index=True, width="stretch")
    c = st.columns(3)
    c[0].metric("합계(금번 신청액)", lib.won(built.합계))
    c[1].metric("문서번호", built.문서번호)
    c[2].metric("사업 남은 한도", lib.won(L.business_limit(doc.사업ID) - (built.금번신청액 or 0)))
    c = st.columns(3)
    c[0].metric("신청 누계", lib.won(built.신청누계액))
    c[1].metric("잔여액", lib.won(built.잔여액))
    if not doc.행사명:
        st.error("행사명을 입력하세요.")
    errors, warnings = lib.findings_box(build.findings)
    accept = True
    if warnings:
        accept = st.checkbox("경고를 확인했습니다. 진행하면 경고가 이슈로 등록됩니다.", key="req_accept")

# ---------------------------------------------------------------- 4. 미리보기·생성
with st.container(border=True):
    st.markdown("**4. 미리보기**")
    file_name = L.file_name(doc.월, doc.행사명 or "행사", "예산신청서")
    preview = {"문서번호": built.문서번호, "작성일": str(built.작성일), "행사명": built.행사명,
               "사업": f"{doc.사업ID} {L.plan[doc.사업ID].사업명}", "일시": f"{doc.시작:%Y-%m-%d %H:%M} ~ {doc.종료:%m-%d %H:%M}",
               "장소": built.장소, "참가대상": built.참가대상, "합계": lib.won(built.합계),
               "신청누계 / 잔여": f"{lib.won(built.신청누계액)} / {lib.won(built.잔여액)}",
               "저장 위치": f"{L.year}/{doc.월}월/{file_name}"}
    st.table(pd.DataFrame(preview.items(), columns=["칸", "값"]).set_index("칸"))
    st.text_area("행사 일정(문서에 들어갈 문장)", built.행사일정, disabled=True, height=90)
    ok = st.checkbox("미리보기 내용을 확인했습니다", key="req_confirm")
    if st.button("신청서 생성·장부 반영", type="primary",
                 disabled=bool(errors) or not accept or not ok or not doc.행사명 or not items):
        before = lib.snapshot_view()
        rows, new_issues = commit_with_issues(L, lib.book(), build, accept_warnings=True)
        data = lib.render_hwpx(built)
        row = rows[0]
        unit = lib.persist(f"장부: {row.문서번호} {row.행사명} 신청서 추가")
        if unit is not None:
            ss.req_result = {"bytes": data, "row": row, "before": before, "unit": unit,
                             "issues": [i.이슈ID for i in new_issues]}
            st.rerun()
