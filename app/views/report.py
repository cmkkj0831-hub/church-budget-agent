"""🧾 집행 보고서: 영수증 이미지만 올리면 Claude가 영수증을 읽고 경과보고를 써서 hwpx를 만든다.
채우는 칸은 경과보고·합계(B)와 설정값뿐, 나머지는 빈칸. 결과: 보고서 hwpx + 영수증 첨부 docx(zip) 내려받기.
아무것도 저장하지 않는다."""
import lib
import pandas as pd
import streamlit as st

from agents import report_agent
from agents.llm import AgentError
from agents.receipt_agent import extract_receipts
from core.report import build_report, file_stem, receipts_total

S = lib.settings()
ss = st.session_state
st.title("🧾 집행 보고서")
st.caption("영수증 이미지를 올리고 [보고서 만들기]를 누르면 경과보고와 합계가 채워진 hwpx와 영수증 첨부 docx를 만듭니다. "
           "주관단체·기안자·연락처·계좌 등은 설정값이 들어가고, 나머지 칸은 빈칸입니다.")


def show_result(r) -> None:
    st.success(f"✅ 보고서를 만들었습니다. 합계 {lib.won(r['total'])}")
    st.download_button("보고서 hwpx + 영수증 docx (zip)", r["zip"], type="primary", file_name=r["zip_name"],
                       mime="application/zip")
    st.caption("파일: " + ", ".join(r["files"]))
    st.markdown("**경과보고**")
    st.text(r["body"])
    st.markdown("**읽은 영수증**")
    st.dataframe(pd.DataFrame(r["rows"]), hide_index=True, width="stretch")
    for w in r["warnings"]:
        st.warning(f"🔎 {w}")
    if st.button("다른 보고서 만들기"):
        for k in [k for k in ss.keys() if k.startswith("rep_")]:
            del ss[k]
        st.rerun()


if ss.get("rep_result"):
    show_result(ss.rep_result)
    st.stop()

lib.uploader("영수증 이미지·PDF (여러 장)", "rep_files", ["jpg", "jpeg", "png", "heic", "pdf"])
if st.button("보고서 만들기", type="primary", disabled=not ss.get("rep_files")):
    try:
        with st.spinner(f"영수증 {len(ss.rep_files)}장을 읽는 중…"):
            receipts = extract_receipts(lib.save_uploads("rep_files"), S, client=lib.llm_client())
        with st.spinner("Claude가 경과보고를 쓰는 중…"):
            comp = report_agent.compose_report(S, "", receipts, client=lib.llm_client())
    except AgentError as e:
        st.error(f"보고서를 만들지 못했습니다: {e}")
        st.stop()
    total = receipts_total(receipts)
    doc = build_report(S, comp.경과보고, total)
    stem = file_stem(receipts, lib.today())
    files = {f"{stem}.hwpx": lib.render_hwpx(doc),
             f"{stem}_영수증첨부.docx": lib.receipts_docx("", "", [r.to_receipt() for r in receipts])}
    ss.rep_result = {
        "zip": lib.zip_bytes(files), "zip_name": f"{stem}.zip", "files": list(files), "total": total,
        "body": comp.경과보고, "warnings": comp.warnings,
        "rows": [{"순번": r.순번, "날짜": r.날짜, "상호": r.상호_확정, "금액": r.금액, "품목": r.품목요약} for r in receipts]}
    st.rerun()
