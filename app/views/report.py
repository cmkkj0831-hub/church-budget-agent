"""🧾 집행 보고서: 신청서 선택 → 영수증 업로드·추출(수정 가능) → 경과보고 초안(수정 가능) → 검증 → 미리보기 → 생성.
결과: 보고서 hwpx + 영수증 첨부 docx(zip), 장부 반영."""
from datetime import date

import lib
import pandas as pd
import streamlit as st

from agents import report_agent
from agents.llm import AgentError
from agents.receipt_agent import extract_receipts
from core.issues import commit_with_issues
from core.models import ReportDoc

lib.require_writable()
L = lib.ledger()
ss = st.session_state
st.title("🧾 집행 보고서")

def show_result(result) -> None:
    row = result["row"]
    st.success(f"✅ {row.문서번호} {row.행사명} 보고서를 만들고 장부(순번 {row.순번})에 반영했습니다.")
    lib.download_guidance(row.월, ", ".join(result["files"]))
    st.download_button("보고서 hwpx + 영수증 docx (zip)", result["zip"], type="primary",
                       file_name=f"{row.파일명.rsplit('.', 1)[0]}.zip", mime="application/zip")
    if result["issues"]:
        st.warning("경고를 이슈로 등록했습니다: " + ", ".join(result["issues"]))
    lib.show_changes(result["before"], result.get("unit"))
    if st.button("다른 보고서 작성"):
        for k in [k for k in ss.keys() if k.startswith("rep_")]:
            del ss[k]
        st.rerun()


if ss.get("rep_result"):  # 생성 뒤에는 결과만(바뀐 장부로 다시 계산한 화면을 보이지 않는다)
    show_result(ss.rep_result)
    st.stop()


pending = lib.unreported_requests()
if not pending:
    st.info("보고서가 없는 신청서가 없습니다.")
    st.stop()

# ---------------------------------------------------------------- 1. 신청서·입력
with st.container(border=True):
    st.markdown("**1. 연결할 신청서와 자료**")
    seq = st.selectbox("신청서(보고서 없는 것)", [r.순번 for r in pending],
                       format_func=lambda s: f"{L.row(s).문서번호} {L.row(s).월} {L.row(s).행사명} "
                                             f"— 신청 {L.row(s).금번신청액:,}원", key="rep_seq")
    request = L.row(seq)
    lib.uploader("영수증 이미지·PDF (여러 장)", "rep_files", ["jpg", "jpeg", "png", "heic", "pdf"])
    st.text_area("경과보고(자연어)", key="rep_note", height=100,
                 placeholder="예: 8/15 미사 후 식대 29만2천원 연재, 플로라 / 8/16 회합 후 식대 4만3천원 연재")
    st.text_area("차기 계획·건의사항(선택)", key="rep_suggestion", height=70)
    if st.button("영수증 읽기", disabled=not ss.get("rep_files")):
        with st.spinner(f"영수증 {len(ss.rep_files)}장을 읽는 중…"):
            try:
                ss.rep_receipts = extract_receipts(lib.save_uploads("rep_files"), L, client=lib.llm_client())
                ss.rep_out = None
                ss.rep_result = None
            except AgentError as e:
                st.error(f"영수증을 읽지 못했습니다: {e}")

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
                            column_config={"날짜": st.column_config.DateColumn(), "금액": st.column_config.NumberColumn(format="%d")})
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
                answer = report_agent.ask(L, request, receipts, ss.rep_note, ss.rep_suggestion, lib.llm_client())
                ss.rep_out, ss.rep_call = answer.input, answer.info
                ss.rep_body_reset = True
                ss.rep_result = None
            except AgentError as e:
                st.error(f"초안을 만들지 못했습니다: {e}")

if not ss.get("rep_out"):
    st.stop()

# 영수증을 고쳐도 LLM을 다시 부르지 않고, 보관한 묶음(rep_out)으로 금액·경고를 다시 계산한다
comp = report_agent.compose(ss.rep_out, receipts, L.settings.교사명단, L.year,
                            bool(ss.rep_note), bool(ss.rep_suggestion))
if ss.pop("rep_body_reset", False):
    ss.rep_body, ss.rep_sugg = comp.경과보고, comp.건의사항

# ---------------------------------------------------------------- 3. 경과보고·참가자·계좌
with st.container(border=True):
    st.markdown("**3. 경과보고 초안** (직접 고칠 수 있습니다)")
    st.caption(ss.rep_call.line())
    if st.button("영수증 수정 내용으로 경과보고 다시 채우기"):
        ss.rep_body, ss.rep_sugg = comp.경과보고, comp.건의사항
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
    st.text_area("건의사항", key="rep_sugg", height=70)
    st.dataframe(pd.DataFrame([{"계정과목": i.계정과목, "내용": i.내용, "금액": i.단가} for i in comp.세부내역]),
                 hide_index=True, width="stretch")
    account = lib.account_override("rep")

draft = ReportDoc(**report_agent.report_fields(request), 작성일=ss.get("rep_date") or lib.today(),
                  세부내역=comp.세부내역, 경과보고=ss.rep_body, 건의사항=ss.rep_sugg,
                  계좌_override=account)
build = L.build_report(draft, request, today=lib.today())
built = build.docs[0]

# ---------------------------------------------------------------- 4. 검증
with st.container(border=True):
    st.markdown("**4. 금액·검증**")
    st.date_input("작성일", key="rep_date", value=lib.today())
    c = st.columns(5)
    c[0].metric("신청액", lib.won(request.금번신청액))
    c[1].metric("합계(B) 실사용", lib.won(built.합계B))
    c[2].metric("본당보조", lib.won(built.본당보조))
    c[3].metric("자체예산", lib.won(built.자체예산))
    c[4].metric("잔액(반납)", lib.won(built.잔액))
    for w in comp.warnings:
        st.warning(f"🔎 {w}")
    errors, warnings = lib.findings_box(build.findings)
    accept = st.checkbox("경고를 확인했습니다. 진행하면 경고가 이슈로 등록됩니다.", key="rep_accept") if warnings else True

# ---------------------------------------------------------------- 5. 미리보기·생성
with st.container(border=True):
    st.markdown("**5. 미리보기**")
    name = L.file_name(request.월번호, request.행사명, "집행보고서")
    st.table(pd.DataFrame({
        "문서번호": built.문서번호, "연결 신청서": request.문서번호, "행사명": built.행사명,
        "예산(A) / 합계(B) / 잔액": f"{lib.won(built.예산A)} / {lib.won(built.합계B)} / {lib.won(built.잔액)}",
        "영수증 첨부": f"{len(receipts)}장", "저장 위치": f"{L.year}/{request.월}/{name}",
    }.items(), columns=["칸", "값"]).set_index("칸"))
    ok = st.checkbox("미리보기 내용을 확인했습니다", key="rep_confirm")
    if st.button("보고서 생성·장부 반영", type="primary", disabled=bool(errors) or not accept or not ok):
        before = lib.snapshot_view()
        rows, new_issues = commit_with_issues(L, lib.book(), build, accept_warnings=True)
        row = rows[0]
        stem = row.파일명.rsplit(".", 1)[0]
        files = {row.파일명: lib.render_hwpx(built),
                 f"{stem}_영수증첨부.docx": lib.receipts_docx(row.문서번호, row.행사명,
                                                         [r.to_receipt() for r in receipts])}
        unit = lib.persist(f"장부: {row.문서번호} {row.행사명} 보고서 추가")
        if unit is not None:
            ss.rep_result = {"zip": lib.zip_bytes(files), "files": list(files), "row": row, "before": before,
                             "unit": unit, "issues": [i.이슈ID for i in new_issues]}
            st.rerun()
