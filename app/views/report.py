"""🧾 집행 보고서(장부 없음): 문서 정보(행사명·월·목적·문서번호·연결 신청서·신청액)·영수증·경과보고 메모·
행사 정보(일시·장소·참가대상·인원) → 영수증 추출(수정 가능) → 경과보고 초안(수정 가능) → 금액·검증 → 생성.
결과: 보고서 hwpx + 영수증 첨부 docx(zip) 내려받기. 금액(본당·자체·잔액)은 core.report가 계산한다."""
from datetime import date

import lib
import pandas as pd
import streamlit as st

from agents import report_agent
from agents.llm import AgentError
from agents.receipt_agent import extract_receipts
from core.models import ReportDoc
from core.report import build_report, doc_number_prefix, file_name

S = lib.settings()
ss = st.session_state
st.title("🧾 집행 보고서")


def show_result(result) -> None:
    st.success(f"✅ {result['title']} 보고서를 만들었습니다.")
    st.info(f"📁 **{S.현재연도}/{result['월']}월** 폴더에 저장하세요. 파일: `{'`, `'.join(result['files'])}`")
    st.download_button("보고서 hwpx + 영수증 docx (zip)", result["zip"], type="primary",
                       file_name=result["zip_name"], mime="application/zip")
    if st.button("다른 보고서 작성"):
        for k in [k for k in ss.keys() if k.startswith("rep_")]:
            del ss[k]
        st.rerun()


if ss.get("rep_result"):
    show_result(ss.rep_result)
    st.stop()


def event_text() -> str:
    """Claude에 알려 줄 행사 정보(채운 칸만)."""
    lines = [f"{k}: {v}" for k, v in (("행사명", ss.get("rep_name", "").strip()), ("대상 월", f"{ss.get('rep_month')}월"),
                                      ("목적", ss.get("rep_purpose", "").strip()),
                                      ("신청액", lib.won(ss.get("rep_applied") or 0))) if v]
    lines += [f"{k}: {v}" for k, v in lib.event_entered("rep").items()]
    return "\n".join(lines)


# ---------------------------------------------------------------- 1. 문서 정보·자료
with st.container(border=True):
    st.markdown("**1. 문서 정보와 자료**")
    a, b = st.columns([3, 1])
    a.text_input("행사명", key="rep_name", placeholder="예: 10월 교사회 운영비")
    b.number_input("대상 월", 1, 12, key="rep_month", value=lib.today().month)
    st.text_input("목적(선택)", key="rep_purpose")
    a, b, c = st.columns(3)
    a.text_input("보고서 문서번호", key="rep_no", value=doc_number_prefix(S), help="예: (집)2026-054")
    b.text_input("연결 신청서 문서번호", key="rep_req_no", placeholder=f"예: {doc_number_prefix(S).replace('(집)', '(예)')}053")
    c.number_input("신청액(연결 신청서 금번신청액, 원)", 0, step=10_000, key="rep_applied")
    lib.uploader("영수증 이미지·PDF (여러 장)", "rep_files", ["jpg", "jpeg", "png", "heic", "pdf"])
    st.text_area("경과보고(자연어, 선택)", key="rep_note", height=100,
                 placeholder="예: 8/15 미사 후 식대 29만2천원 연재, 플로라 / 8/16 회합 후 식대 4만3천원 연재")
    event_box = st.container()  # 행사 정보(영수증을 읽은 뒤 빈 날짜를 채울 수 있게 버튼 처리 뒤에 그린다)
    if st.button("영수증 읽기", disabled=not ss.get("rep_files")):
        with st.spinner(f"영수증 {len(ss.rep_files)}장을 읽는 중…"):
            try:
                ss.rep_receipts = extract_receipts(lib.save_uploads("rep_files"), S, client=lib.llm_client())
                ss.rep_out = None
                first, last = lib.receipt_span(ss.rep_receipts)
                lib.fill_event_blanks("rep", {"시작일": first, "종료일": last})
            except AgentError as e:
                st.error(f"영수증을 읽지 못했습니다: {e}")
    with event_box:
        lib.event_fields("rep")
        st.caption("날짜를 비워 두면 영수증을 읽은 뒤 영수증 날짜로 채웁니다. 장소·참가대상을 비우면 설정 기본값이 들어갑니다.")

receipts = ss.get("rep_receipts")
if not receipts:
    st.stop()

# ---------------------------------------------------------------- 2. 추출 결과(수정 가능)
with st.container(border=True):
    st.markdown("**2. 영수증 추출 결과** (금액·날짜·실제 상호를 고칠 수 있습니다)")
    calls = [r.call for r in receipts if r.call]
    if calls:
        st.caption(f"{len(calls)}회 호출 · 모델 {', '.join(sorted({c.model for c in calls}))} · "
                   f"{sum(c.seconds for c in calls):.0f}초 · 토큰 {sum(c.input_tokens for c in calls):,}/"
                   f"{sum(c.output_tokens for c in calls):,}")
    df = pd.DataFrame([{
        "순번": r.순번, "파일": r.파일.split("/")[-1], "날짜": r.날짜, "상호(영수증)": r.상호, "실제 상호": r.상호_사용자,
        "금액": r.금액, "품목": r.품목요약, "승인번호": r.승인번호, "대행사": "⚠️" if r.대행사표기 else "",
        "확인": r.확인사유 if r.확인필요 or r.대행사표기 else ""} for r in receipts])
    edited = st.data_editor(df, hide_index=True, width="stretch", key="rep_table",
                            disabled=["순번", "파일", "상호(영수증)", "대행사", "확인"],
                            column_config={"날짜": st.column_config.DateColumn(),
                                           "금액": st.column_config.NumberColumn(format="%d")})
    for r, row in zip(receipts, edited.to_dict("records")):
        r.날짜 = row["날짜"] if isinstance(row["날짜"], date) else (pd.to_datetime(row["날짜"]).date() if row["날짜"] else None)
        r.금액 = None if pd.isna(row["금액"]) else int(row["금액"])
        r.상호_사용자 = str(row["실제 상호"] or "").strip()
        r.품목요약 = str(row["품목"] or "")
        r.승인번호 = str(row["승인번호"] or "")
    st.metric("영수증 합계", lib.won(sum(r.금액 or 0 for r in receipts)))
    if st.button("보고서 초안 만들기", type="primary"):
        with st.spinner("Claude가 세부내역·경과보고를 정리하는 중…"):
            try:
                answer = report_agent.ask(S, event_text(), receipts, ss.rep_note, lib.llm_client())
                ss.rep_out, ss.rep_call = answer.input, answer.info
                ss.rep_body_reset = True
            except AgentError as e:
                st.error(f"초안을 만들지 못했습니다: {e}")

if not ss.get("rep_out"):
    st.stop()

# 영수증을 고쳐도 LLM을 다시 부르지 않고, 보관한 묶음(rep_out)으로 금액·경고를 다시 계산한다
comp = report_agent.compose(ss.rep_out, receipts, S.교사명단, S.현재연도, bool(ss.rep_note))
if ss.pop("rep_body_reset", False):
    ss.rep_body = comp.경과보고

# ---------------------------------------------------------------- 3. 경과보고·참가자·계좌
with st.container(border=True):
    st.markdown("**3. 경과보고 초안** (직접 고칠 수 있습니다)")
    st.caption(ss.rep_call.line())
    if st.button("영수증 수정 내용으로 경과보고 다시 채우기"):
        ss.rep_body = comp.경과보고
    for k, choice in enumerate(comp.참가자_선택):
        key = f"rep_pick_{k}"
        prev = ss.get(f"{key}_applied", choice["입력"])  # 경과보고에 지금 들어 있는 이름
        pick = st.selectbox(f"'{choice['입력']}'은 누구인가요? ({choice['줄']})", choice["후보"], index=None,
                            placeholder="선택하세요(고르기 전에는 적힌 그대로 둡니다)", key=key)
        if pick and pick != prev:
            ss.rep_body = "\n".join(ln.replace(prev, pick) if ln.startswith(choice["줄"]) else ln
                                     for ln in ss.rep_body.split("\n"))
            ss[f"{key}_applied"] = pick
    st.text_area("경과보고", key="rep_body", height=180)
    st.dataframe(pd.DataFrame([{"계정과목": i.계정과목, "내용": i.내용, "금액": i.단가} for i in comp.세부내역]),
                 hide_index=True, width="stretch")
    account = lib.account_override("rep")

draft = ReportDoc(**lib.event_values("rep"), 월=int(ss.rep_month), 행사명=ss.rep_name.strip(),
                  목적=ss.rep_purpose.strip(), 문서번호=ss.rep_no.strip(), 연결문서번호=ss.rep_req_no.strip(),
                  작성일=ss.get("rep_date") or lib.today(), 세부내역=comp.세부내역, 경과보고=ss.rep_body,
                  계좌_override=account)
build = build_report(draft, S, int(ss.rep_applied or 0))
built = build.doc

# ---------------------------------------------------------------- 4. 금액·검증
with st.container(border=True):
    st.markdown("**4. 금액·검증**")
    st.date_input("작성일", key="rep_date", value=lib.today())
    c = st.columns(5)
    c[0].metric("신청액", lib.won(int(ss.rep_applied or 0)))
    c[1].metric("합계(B) 실사용", lib.won(built.합계B))
    c[2].metric("본당보조", lib.won(built.본당보조))
    c[3].metric("자체예산", lib.won(built.자체예산))
    c[4].metric("잔액(반납)", lib.won(built.잔액))
    for w in comp.warnings:
        st.warning(f"🔎 {w}")
    for e in build.errors:
        st.error(f"⛔ {e}")
    for w in build.warnings:
        st.warning(f"⚠️ {w}")
    if not (build.errors or build.warnings or comp.warnings):
        st.success("검증 통과")

# ---------------------------------------------------------------- 5. 미리보기·생성
with st.container(border=True):
    st.markdown("**5. 미리보기**")
    name = file_name(built)
    st.table(pd.DataFrame({
        "문서번호": built.문서번호 or "(빈칸)", "연결 신청서": built.연결문서번호 or "(빈칸)", "행사명": built.행사명,
        "예산(A) / 합계(B) / 잔액": f"{lib.won(built.예산A)} / {lib.won(built.합계B)} / {lib.won(built.잔액)}",
        "영수증 첨부": f"{len(receipts)}장", "저장 위치": f"{S.현재연도}/{built.월}월/{name}",
    }.items(), columns=["칸", "값"]).set_index("칸"))
    if st.button("보고서 만들기", type="primary", disabled=bool(build.errors)):
        stem = name.rsplit(".", 1)[0]
        files = {name: lib.render_hwpx(built),
                 f"{stem}_영수증첨부.docx": lib.receipts_docx(built.문서번호, built.행사명,
                                                         [r.to_receipt() for r in receipts])}
        ss.rep_result = {"zip": lib.zip_bytes(files), "files": list(files), "zip_name": f"{stem}.zip",
                         "title": f"{built.문서번호} {built.행사명}".strip(), "월": built.월}
        st.rerun()
