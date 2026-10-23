"""⚠️ 오류 관리: 이슈 목록·카드(쉬운 설명·근거) → 자연어/첨부 hwpx 분석 → 처리안 → 반영.
이력 탭: 해결된 이슈, 반영 단위 이력과 [되돌리기]. 장부 재검사: 새 이슈 후보 중 고른 것만 등록.
문서 삭제: 앱에서 만든 신청서·보고서를 잘못 만들었을 때 장부에서 뺀다(core.delete, 이력·되돌리기 가능)."""
import tempfile
from pathlib import Path

import lib
import pandas as pd
import streamlit as st

from agents import issue_agent
from agents.llm import AgentError
from core.issues import apply_resolution, budget_impact, create_issue, issue_color
from core.ledger import BlockedError, report_amounts
from core.models import ISSUE_TYPES_ALL
from core.validate import scan_ledger
from hwpx.reader import UnknownFormError, read_document

from core.changes import units  # noqa: E402
from core.delete import apply_delete, deletable, plan_delete  # noqa: E402

L, B = lib.ledger(), lib.book()
ss = st.session_state
st.title("⚠️ 오류 관리")
writable = not L.read_only

breaks = L.cumulative_breaks()
trace = {t.순번: t for t in L.cumulative_trace()}
impact = {i.이슈ID: budget_impact(L, B, i, breaks, {k: v.차이 for k, v in trace.items()}) for i in B.issues}
c = st.columns(3)
c[0].metric("미해결", sum(i.상태 == "미해결" for i in B.issues))
c[1].metric("확인중", sum(i.상태 == "확인중" for i in B.issues))
c[2].metric("해결", sum(not i.열림 for i in B.issues))

tab_list, tab_history, tab_scan, tab_delete = st.tabs(["이슈", "이력", "장부 재검사", "문서 삭제"])

# ================================================================ 이슈 목록·카드
with tab_list:
    a, b, m = st.columns(3)
    states = a.multiselect("상태", ["미해결", "확인중", "해결-정정", "해결-설명됨"], default=["미해결", "확인중"])
    kinds = b.multiselect("유형", ISSUE_TYPES_ALL)
    months = m.multiselect("월", sorted({L.row(s).월 for i in B.issues for s in i.대상순번},
                                       key=lambda x: int(x.rstrip("월"))))
    shown = [i for i in B.issues if (not states or i.상태 in states) and (not kinds or i.유형 in kinds)
             and (not months or any(L.row(s).월 in months for s in i.대상순번))]
    table = pd.DataFrame([{
        "이슈ID": i.이슈ID, "상태": i.상태 + (" · 검토 대기" if i.검토대기 else ""), "유형": i.유형,
        "대상 문서": ", ".join(f"{L.row(s).문서번호} {L.row(s).행사명}" for s in i.대상순번),
        "월": ", ".join(dict.fromkeys(L.row(s).월 for s in i.대상순번)), "설명": i.설명,
        "예산 영향": impact[i.이슈ID]} for i in shown])
    if table.empty:
        st.info("조건에 맞는 이슈가 없습니다.")
    else:
        st.dataframe(table.style.map(lambda v: f"color: #{issue_color(v)}", subset=["유형"]),
                     hide_index=True, width="stretch",
                     column_config={"예산 영향": st.column_config.NumberColumn(format="%d원")})

        issue_id = st.selectbox("이슈 선택", [i.이슈ID for i in shown], key="iss_id",
                                format_func=lambda x: f"{x} — {B.get(x).설명[:50]}")
        issue = B.get(issue_id)

        # ---------------- 카드 위: 설명·근거
        with st.container(border=True):
            st.markdown(f"### {issue.이슈ID} · <span style='color:#{issue_color(issue.유형)}'>{issue.유형}</span> · {issue.상태}",
                        unsafe_allow_html=True)
            st.markdown(f"**{issue.설명}**")
            explained = ss.setdefault("iss_explain", {})
            if issue.이슈ID in explained:
                st.info(explained[issue.이슈ID])
            elif st.button("쉬운 설명 보기(Claude)", key=f"explain_{issue_id}"):
                with st.spinner("설명을 쓰는 중…"):
                    try:
                        text, calls, notes = issue_agent.explain_issue(issue_id, L, B, client=lib.llm_client())
                        explained[issue.이슈ID] = text
                        st.rerun()
                    except AgentError as e:
                        st.error(str(e))
            st.caption(f"예산 영향: {impact[issue.이슈ID]:,}원 · 근거: {issue.근거}")
            rows = []
            for s in issue.대상순번:
                r = L.row(s)
                if r.신청서:
                    t = trace.get(s)
                    rows += [{"문서": r.문서번호, "항목": "신청누계액", "장부": r.신청누계액, "계산": t.계산 if t else None},
                             {"문서": r.문서번호, "항목": "잔여액", "장부": r.잔여액,
                              "계산": (r.예산배정액 or 0) - (r.신청누계액 or 0)},
                             {"문서": r.문서번호, "항목": "합계", "장부": r.합계,
                              "계산": (r.본당보조 or 0) + (r.자체예산 or 0)}]
                else:
                    req = L.find_ref(r.연결문서번호)
                    if req is not None and r.합계 is not None:
                        sub, own, total, _, bal = report_amounts(req.금번신청액 or 0, r.합계)
                        rows += [{"문서": r.문서번호, "항목": k, "장부": v, "계산": w} for k, v, w in
                                 (("본당보조", r.본당보조, sub), ("자체예산", r.자체예산, own), ("잔액", r.잔액, bal))]
            if rows:
                df = pd.DataFrame(rows)
                df["다름"] = ["⚠️" if a != b else "" for a, b in zip(df["장부"], df["계산"])]
                st.markdown("장부 값 대비 계산값")
                st.dataframe(df, hide_index=True, width="stretch")
            if issue.검토메모:
                st.warning(f"📝 회계담당 설명(검토 대기): {issue.검토메모}" if issue.검토대기 else f"📝 {issue.검토메모}")
            if not issue.열림:
                st.success(f"{issue.상태} — {issue.해결내용} ({issue.처리자}, {issue.처리일})")

        # ---------------- 카드 아래: 입력·분석
        if issue.열림 and writable:
            with st.container(border=True):
                st.markdown("**설명·자료 입력**")
                text_key = f"iss_text_{issue_id}"
                if text_key not in ss and issue.검토메모:
                    ss[text_key] = issue.검토메모.split(" (", 1)[0]
                note = st.text_area("무슨 일이 있었는지 알려 주세요", key=text_key, height=90,
                                    placeholder="예: 부서 운영비는 철회됐어 / 원본 보고서 다시 보니 잔액 0원이야")
                if st.button("메모만 저장하고 '확인중'으로 표시", disabled=not note.strip(),
                             help="아직 확인 중일 때. 나중에 이어서 분석할 수 있습니다."):
                    issue.검토메모 = f"{note.strip()} ({lib.today().isoformat()} 회계담당)"
                    issue.상태 = "확인중"
                    if lib.persist(f"이슈: {issue.이슈ID} 확인중(메모)", issue.이슈ID) is not None:
                        st.rerun()
                up = st.file_uploader("관련 신청서·보고서 hwpx(선택)", type=["hwpx"], key=f"iss_file_{issue_id}")
                attachment = None
                if up is not None:
                    with tempfile.TemporaryDirectory() as d:
                        p = Path(d) / up.name
                        p.write_bytes(up.getvalue())
                        try:
                            attachment = read_document(p)
                            st.caption(f"첨부: {attachment.kind} {attachment.doc.문서번호} {attachment.doc.행사명}"
                                       + (f" · 읽기 실패 칸 {', '.join(f.칸 for f in attachment.failures)}"
                                          if attachment.failures else ""))
                        except UnknownFormError as e:
                            st.error(f"서식1·2 hwpx가 아닙니다: {e}")
                if st.button("분석", type="primary", disabled=not note.strip() and attachment is None):
                    with st.spinner("Claude가 이슈를 검토하는 중…"):
                        try:
                            ss[f"iss_result_{issue_id}"] = issue_agent.assist_issue(
                                issue_id, L, B, note, attachment, 처리자=lib.HANDLER, client=lib.llm_client())
                        except AgentError as e:
                            st.error(str(e))

            # ---------------- 처리안·반영
            result = ss.get(f"iss_result_{issue_id}")
            if result is not None and writable:
                with st.container(border=True):
                    st.markdown(f"**처리안: {result.해결유형}**")
                    st.caption(" · ".join(c.line() for c in result.calls))
                    st.info(result.설명문)
                    if result.첨부대조:
                        st.markdown("첨부 문서 ↔ 장부 대조")
                        st.dataframe(pd.DataFrame(result.첨부대조), hide_index=True, width="stretch")
                    for p in result.problems:
                        st.warning(p)
                    for q in result.추가질문:
                        st.markdown(f"❓ {q}")
                    for q in result.남은의문:
                        st.markdown(f"🔎 {q}")
                    if result.resolution is not None and result.verification is not None:
                        v = result.verification
                        st.markdown("장부 변경 전후")
                        st.dataframe(pd.DataFrame([{**d, "변경전": str(d["변경전"]), "변경후": str(d["변경후"])}
                                                   for d in result.diff]) if result.diff else
                                     pd.DataFrame([{"변경": "값 변경 없음(사유만 기록)"}]),
                                     hide_index=True, width="stretch")
                        k = st.columns(3)
                        k[0].metric("신청누계(재계산)", lib.won(v.누계_후), f"{v.누계_후 - v.누계_전:+,}원")
                        k[1].metric("잔여", lib.won(v.잔여_후))
                        k[2].metric("함께 해결(제안)", ", ".join(result.resolution.함께해결) or "-")
                        for f in v.경고:
                            st.warning(f"반영 후에도 남는 이상: [{f.유형}] {f.설명}")
                        related = [i for i in B.open_issues() if i.이슈ID != issue_id and
                                   (set(i.대상순번) & set(issue.대상순번) or i.이슈ID in result.resolution.함께해결)]
                        together = st.multiselect(
                            "함께 해결할 이슈(같은 사유로 닫을 것만 남기세요)", [i.이슈ID for i in related],
                            default=[x for x in result.resolution.함께해결 if any(i.이슈ID == x for i in related)],
                            format_func=lambda x: f"{x} {B.get(x).유형}: {B.get(x).설명[:60]}",
                            key=f"iss_together_{issue_id}")
                        content = st.text_area("해결내용(이슈에 기록)", result.resolution.내용,
                                               key=f"iss_content_{issue_id}")
                        ok = st.checkbox("변경 전후와 해결내용을 확인했습니다", key=f"iss_ok_{issue_id}")
                        if st.button("반영", type="primary", disabled=not ok or not content.strip()):
                            before = lib.snapshot_view()
                            res = result.resolution.model_copy(update={"내용": content.strip(), "처리일": lib.today(),
                                                                   "함께해결": list(together)})
                            try:
                                apply_resolution(L, B, issue_id, res)
                            except BlockedError as e:
                                st.error(f"반영하지 못했습니다: {e}")
                                st.stop()
                            label = "해결(정정)" if res.방식 == "정정" else "해결(설명됨)"
                            unit = lib.persist(f"이슈: {issue_id} {label}", issue_id)
                            if unit is not None:
                                ss.iss_done = {"id": issue_id, "label": label, "before": before, "unit": unit}
                                del ss[f"iss_result_{issue_id}"]
                                st.rerun()

        done = ss.get("iss_done")
        if done:
            st.success(f"✅ {done['id']} {done['label']} 반영 — 장부·이슈·이력이 갱신되고 비고에서 빠졌습니다.")
            lib.show_changes(done["before"], done.get("unit"))

# ================================================================ 이력
with tab_history:
    closed = [i for i in B.issues if not i.열림]
    st.markdown(f"**해결된 이슈 {len(closed)}건**")
    st.dataframe(pd.DataFrame([{"이슈ID": i.이슈ID, "유형": i.유형, "상태": i.상태, "설명": i.설명,
                                "해결내용": i.해결내용, "처리자": i.처리자, "처리일": i.처리일} for i in closed]),
                 hide_index=True, width="stretch")
    st.markdown("**반영 이력** — 반영 하나를 통째로 되돌릴 수 있습니다(장부·이슈 함께 복원, 되돌리기도 이력에 남음).")
    unit_rows = units(B)
    if not unit_rows:
        st.caption("아직 반영 이력이 없습니다.")
    else:
        st.dataframe(pd.DataFrame([{**u, "되돌림": "되돌림" if u["되돌림"] else ""} for u in unit_rows]),
                     hide_index=True, width="stretch")
        undoable = [u["이력ID"] for u in unit_rows if not u["되돌림"] and not u["사유"].startswith("되돌리기")
                    and u["이력ID"] != "H-0001"]
        if writable and undoable:
            pick = st.selectbox("되돌릴 반영", undoable, key="iss_undo_pick",
                                format_func=lambda x: next(f"{u['이력ID']} · {u['일시']} · {u['사유']}"
                                                           for u in unit_rows if u["이력ID"] == x))
            detail = [h for h in B.history if h.이력ID == pick]
            st.dataframe(pd.DataFrame([{"대상": h.대상, "문서/이슈": h.이슈ID or h.순번, "열": h.열,
                                        "변경전": h.변경전[:60], "변경후": h.변경후[:60]} for h in detail]),
                         hide_index=True, width="stretch")
            ok = st.checkbox("위 변경을 모두 되돌립니다(장부·이슈를 반영 전으로 복원)", key="iss_undo_ok")
            if st.button("되돌리기", disabled=not ok):
                before = lib.snapshot_view()
                try:
                    new = lib.undo(pick)
                except BlockedError as e:
                    st.error(str(e))
                    st.stop()
                if new is not None:
                    ss.iss_undone = {"unit": pick, "new": new, "before": before}
                    st.rerun()
        done = ss.get("iss_undone")
        if done:
            st.success(f"↩️ {done['unit']}을(를) 되돌렸습니다(이력 {done['new']}).")
            lib.show_changes(done["before"], done["new"])
    with st.expander(f"항목별 이력 {len(B.history)}건"):
        st.dataframe(pd.DataFrame([h.model_dump() for h in B.history]), hide_index=True, width="stretch")

# ================================================================ 장부 재검사
with tab_scan:
    st.caption("core.validate.scan_ledger로 장부 전체를 다시 검사해, 등록된 이슈와 겹치지 않는 새 이상만 보여 줍니다.")
    if st.button("장부 재검사"):
        ss.iss_scan = scan_ledger(L, B.issues, today=lib.today())
    found = ss.get("iss_scan")
    if found is not None:
        if not found:
            st.success("새 이상 사항이 없습니다.")
        else:
            df = pd.DataFrame([{"등록": False, "수준": f.수준, "유형": f.유형, "대상": ", ".join(f.문서번호),
                                "설명": f.설명} for f in found])
            picked = st.data_editor(df, hide_index=True, width="stretch", key="iss_scan_table",
                                    disabled=["수준", "유형", "대상", "설명"])
            chosen = [f for f, p in zip(found, picked["등록"]) if p]
            if writable and st.button(f"선택한 {len(chosen)}건 이슈 등록", disabled=not chosen):
                ids = [create_issue(B, f, 근거=f"장부 재검사({lib.today().isoformat()}): {f.설명}").이슈ID for f in chosen]
                if lib.persist(f"이슈: {', '.join(ids)} 등록(장부 재검사)") is not None:
                    ss.iss_scan = None
                    st.success("등록했습니다: " + ", ".join(ids))
                    st.rerun()

# ================================================================ 문서 삭제
with tab_delete:
    st.markdown("잘못 만든 **신청서·보고서를 장부에서 지웁니다.** 장부·대시보드·엑셀에서 함께 빠지고, 지운 내용은 이력에 남아 "
                "**이력 탭에서 되돌릴 수 있습니다.**")
    st.caption("앱에서 만든 문서만 지울 수 있습니다. 엑셀에서 옮겨 온 원본 문서는 '이슈' 탭에서 설명·취소로 처리하세요. "
               "신청서를 지우면 연결된 보고서도 함께 지워지고, 보고서만 지우면 신청서는 보고 전 상태로 돌아갑니다.")
    done = ss.get("del_done")
    if done:
        st.success(f"🗑️ {done['names']}을(를) 장부에서 지웠습니다. 내려받아 둔 hwpx 파일도 지우세요.")
        lib.show_changes(done["before"], done["unit"])
        if st.button("다른 문서 지우기"):
            ss.del_done = None
            st.rerun()
    elif not writable:
        st.info(f"{L.year}년은 마감된 연도라 문서를 지울 수 없습니다.")
    else:
        rows = deletable(L, B)
        if not rows:
            st.info("지울 수 있는 문서(앱에서 만든 문서)가 없습니다.")
        else:
            label = {r.순번: f"{r.문서번호} · {r.행사명} · {r.구분} · "
                             f"{lib.won(r.금번신청액 if r.신청서 else r.합계)} · {r.작성일}" for r in rows}
            picked = st.multiselect("지울 문서", [r.순번 for r in reversed(rows)], format_func=label.get, key="del_pick")
            reason = st.text_input("삭제 사유(이력과 이슈에 남습니다)", key="del_reason",
                                   placeholder="예: 금액을 잘못 넣어 다시 만들 예정")
            if picked:
                try:
                    plan = plan_delete(L, B, picked)
                except BlockedError as e:
                    st.error(str(e))
                    st.stop()
                st.markdown("**미리보기 — 장부에서 빠질 문서**")
                st.dataframe(pd.DataFrame([{"문서번호": r.문서번호, "구분": r.구분, "행사명": r.행사명, "작성일": r.작성일,
                                            "금번신청액": r.금번신청액, "합계": r.합계, "잔액": r.잔액} for r in plan.rows]),
                             hide_index=True, width="stretch")
                after = L.copy()
                after.rows = [r for r in after.rows if r.순번 not in set(plan.seqs)]
                c = st.columns(3)
                c[0].metric("다음 문서번호", f"{after.next_doc_number():03d}",
                            after.next_doc_number() - L.next_doc_number())
                c[1].metric("신청누계(재계산)", lib.won(after.cumulative_requested()),
                            f"{after.cumulative_requested() - L.cumulative_requested():+,}원")
                c[2].metric("장부 행", len(after.rows), len(after.rows) - len(L.rows))
                for req, values in plan.unlink:
                    st.info(f"{req.문서번호}는 보고서 연결이 풀리고 상태가 '{values['상태']}'(으)로 돌아갑니다.")
                if plan.close_issues:
                    st.info("함께 닫을 이슈(해결-설명됨, 문서 삭제): " + ", ".join(plan.close_issues))
                for w in plan.warnings:
                    st.warning(w)
                ok = st.checkbox("위 문서를 장부에서 지웁니다", key="del_ok")
                if st.button("문서 삭제", type="primary", disabled=not (ok and reason.strip())):
                    before = lib.snapshot_view()
                    names = ", ".join(r.문서번호 for r in plan.rows)
                    first = plan.rows[0]
                    apply_delete(L, B, plan, reason, lib.HANDLER, lib.today())
                    unit = lib.persist(f"장부: {names} {first.행사명} 삭제({reason.strip()})")
                    if unit is not None:
                        ss.del_done = {"names": names, "before": before, "unit": unit}
                        for k in ("del_pick", "del_reason", "del_ok"):
                            ss.pop(k, None)
                        st.rerun()
