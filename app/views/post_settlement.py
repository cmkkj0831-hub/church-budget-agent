"""🔁 사후 정산: 사업 선택 → 영수증·경과보고 → 보고서 확정 → 한도 적용 신청서 역생성 → 한 번에 생성."""
import lib
import pandas as pd
import streamlit as st

from agents import report_agent
from agents.llm import AgentError
from agents.receipt_agent import extract_receipts
from core.ledger import BlockedError
from core.issues import commit_with_issues
from core.models import ReportDoc

lib.require_writable()
L = lib.ledger()
ss = st.session_state
st.title("🔁 사후 정산")
st.caption("신청서 없이 먼저 쓴 비용을 보고서로 확정하고, 사업 한도 안에서 신청서를 거꾸로 만듭니다. "
           "문서번호는 신청서 N, 보고서 N+1, 작성일은 같은 날입니다.")

def show_result(result) -> None:
    rows = result["rows"]
    st.success(f"✅ 사후정산 {rows[0].문서번호}(신청서)·{rows[1].문서번호}(보고서)를 만들고 장부에 반영했습니다.")
    lib.download_guidance(rows[0].월, ", ".join(result["files"]))
    st.download_button("신청서 + 보고서 + 영수증 (zip)", result["zip"], type="primary",
                       file_name=f"{rows[0].월}_{rows[0].행사명}_사후정산.zip", mime="application/zip")
    if result["issues"]:
        st.warning("경고를 이슈로 등록했습니다: " + ", ".join(result["issues"]))
    lib.show_changes(result["before"], result.get("unit"))
    if st.button("새 사후정산"):
        for k in [k for k in ss.keys() if k.startswith("ps_")]:
            del ss[k]
        st.rerun()


if ss.get("ps_result"):  # 생성 뒤에는 결과만(바뀐 장부로 다시 계산한 화면을 보이지 않는다)
    show_result(ss.ps_result)
    st.stop()


# ---------------------------------------------------------------- 1. 사업·자료
with st.container(border=True):
    st.markdown("**1. 사업과 자료**")
    a, b, c = st.columns([2, 1, 2])
    pid = a.selectbox("사업", list(L.plan), key="ps_pid",
                      format_func=lambda p: f"{p} {L.plan[p].사업명} (남은 한도 {L.business_limit(p):,}원)")
    b.number_input("대상 월", 1, 12, key="ps_month", value=lib.today().month)
    c.text_input("행사명", key="ps_name", placeholder=f"예: {lib.today().month}월 {L.plan[pid].사업명}")
    lib.uploader("영수증 이미지·PDF (여러 장)", "ps_files", ["jpg", "jpeg", "png", "heic", "pdf"])
    st.text_area("경과보고(자연어)", key="ps_note", height=100)
    st.text_area("차기 계획·건의사항(선택)", key="ps_suggestion", height=60)
    if st.button("영수증 읽고 보고서 초안 만들기", type="primary", disabled=not ss.get("ps_files")):
        try:
            with st.spinner("영수증을 읽는 중…"):
                ss.ps_receipts = extract_receipts(lib.save_uploads("ps_files"), L, client=lib.llm_client())
            with st.spinner("보고서 초안을 쓰는 중…"):
                note = "\n\n[사후정산] 신청서 없이 보고서를 먼저 작성한다."
                answer = report_agent.ask(L, None, ss.ps_receipts, ss.ps_note, ss.ps_suggestion, lib.llm_client(), note)
                ss.ps_out, ss.ps_call = answer.input, answer.info
                ss.ps_body_reset, ss.ps_result = True, None
        except AgentError as e:
            st.error(f"초안을 만들지 못했습니다: {e}")

if not ss.get("ps_out"):
    st.stop()
receipts = ss.ps_receipts
comp = report_agent.compose(ss.ps_out, receipts, L.settings.교사명단, L.year,
                            bool(ss.ps_note), bool(ss.ps_suggestion))
if ss.pop("ps_body_reset", False):
    ss.ps_body, ss.ps_sugg = comp.경과보고, comp.건의사항
event_name = (ss.get("ps_name") or "").strip() or (comp.행사명_제안 or "")
if not (ss.get("ps_name") or "").strip() and comp.행사명_제안:
    st.info(f"행사명을 비워 두어 Claude 제안 '{comp.행사명_제안}'을 씁니다(위에서 바꿀 수 있습니다).")

# ---------------------------------------------------------------- 2. 영수증·경과보고
with st.container(border=True):
    st.markdown("**2. 영수증과 경과보고 확인**")
    st.caption(ss.ps_call.line())
    df = pd.DataFrame([{"순번": r.순번, "날짜": r.날짜, "상호": r.상호_확정, "금액": r.금액, "품목": r.품목요약,
                        "확인": r.확인사유 if r.확인필요 or r.대행사표기 else ""} for r in receipts])
    edited = st.data_editor(df, hide_index=True, width="stretch", key="ps_table",
                            disabled=["순번", "확인"], column_config={"금액": st.column_config.NumberColumn(format="%d")})
    for r, row in zip(receipts, edited.to_dict("records")):
        r.금액 = None if pd.isna(row["금액"]) else int(row["금액"])
        if row["상호"] != r.상호_확정:
            r.상호_사용자 = str(row["상호"] or "")
    if comp.사업ID_후보 and comp.사업ID_후보[0].get("사업ID") != pid:
        st.info("Claude가 제안한 사업: " + ", ".join(f"{c['사업ID']}({c['근거']})" for c in comp.사업ID_후보))
    st.text_area("경과보고", key="ps_body", height=150)
    st.text_area("건의사항", key="ps_sugg", height=60)
    for w in comp.warnings:
        st.warning(f"🔎 {w}")

# ---------------------------------------------------------------- 3. 한도 계산·검증
draft = ReportDoc(월=int(ss.ps_month), 사업ID=pid, 행사명=event_name, 작성일=lib.today(),
                  세부내역=comp.세부내역, 경과보고=ss.ps_body, 건의사항=ss.ps_sugg)
used = sum(i.단가 * i.수량 for i in draft.세부내역)
limit = L.business_limit(pid)
with st.container(border=True):
    st.markdown("**3. 한도 계산**")
    apply = min(used, max(limit, 0))
    st.table(pd.DataFrame({"사업 한도(계획 − 실집행)": lib.won(limit), "실사용액": lib.won(used),
                           "신청액(= min(실사용, 한도))": lib.won(apply),
                           "자체예산(초과분)": lib.won(used - apply)}.items(), columns=["항목", "금액"]).set_index("항목"))
    build = None
    if not draft.행사명:
        st.error("행사명을 입력하세요.")
    else:
        try:
            build = L.build_post_settlement(draft, today=lib.today())
        except BlockedError as e:
            st.error(f"⛔ {e}")
    if build is None:
        st.stop()
    req, rep = build.docs
    errors, warnings = lib.findings_box(build.findings)
    accept = st.checkbox("경고를 확인했습니다. 진행하면 경고가 이슈로 등록됩니다.", key="ps_accept") if warnings else True

# ---------------------------------------------------------------- 4. 미리보기·생성
with st.container(border=True):
    st.markdown("**4. 미리보기**")
    st.table(pd.DataFrame([
        {"문서": "신청서", "문서번호": req.문서번호, "작성일": req.작성일, "금번신청액": lib.won(req.금번신청액),
         "자체예산": lib.won(req.자체예산), "합계": lib.won(req.합계), "누계/잔여": f"{req.신청누계액:,} / {req.잔여액:,}"},
        {"문서": "보고서", "문서번호": rep.문서번호, "작성일": rep.작성일, "금번신청액": lib.won(rep.본당보조),
         "자체예산": lib.won(rep.자체예산), "합계": lib.won(rep.합계B), "누계/잔여": f"잔액 {rep.잔액:,}"}]))
    ok = st.checkbox("미리보기 내용을 확인했습니다", key="ps_confirm")
    if st.button("신청서·보고서 생성·장부 반영", type="primary", disabled=bool(errors) or not accept or not ok):
        before = lib.snapshot_view()
        rows, new_issues = commit_with_issues(L, lib.book(), build, accept_warnings=True)
        files = {}
        for doc, row in zip(build.docs, rows):
            files[row.파일명] = lib.render_hwpx(doc)
        rep_row = rows[1]
        files[f"{rep_row.파일명.rsplit('.', 1)[0]}_영수증첨부.docx"] = lib.receipts_docx(
            rep_row.문서번호, rep_row.행사명, [r.to_receipt() for r in receipts])
        unit = lib.persist(f"장부: {rows[0].문서번호}·{rows[1].문서번호} {rows[0].행사명} 사후정산 추가")
        if unit is not None:
            ss.ps_result = {"zip": lib.zip_bytes(files), "files": list(files), "rows": rows, "before": before,
                            "unit": unit, "issues": [i.이슈ID for i in new_issues]}
            st.rerun()
