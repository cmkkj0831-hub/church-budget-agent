"""📊 대시보드: 연도 선택(지난 연도 읽기 전용), 회계 현황(KPI·누계 두 기준·차트·장부 표·xlsx),
사업계획서(집행률·연말 예상·월별 배분·개요·xlsx), 사용량(월별·기능별·환경별, 예상 비용)."""
import io
import tempfile
from pathlib import Path

import lib
import pandas as pd
import streamlit as st

from core.export import export_plan_xlsx, export_xlsx
from core.issues import cumulative_reconciliation, issue_color
from core.usage import annual_projection, load_usage, summarize

ss = st.session_state
years = sorted((int(y) for y in lib.ledger().settings.연도별), reverse=True)
current = lib.ledger().settings.현재연도
pick = st.selectbox("회계연도", years, index=years.index(ss.get("year", current)), key="dash_year",
                    format_func=lambda y: f"{y}년" + (" (현재)" if y == current else " (지난 연도·읽기 전용)"))
if pick != ss.get("year"):
    lib.load(year=pick)
    st.rerun()
L, B = lib.ledger(), lib.book()
year = L.year
st.title(f"📊 {year} 초등부 교사회 회계 대시보드")

open_issues = B.open_issues()
c1, c2 = st.columns([1, 4])
with c1:
    st.page_link(st.session_state._pages["issues"], label=f"미해결 이슈 {len(open_issues)}건 →", icon="⚠️")
budget_warn = [i for i in open_issues if i.유형 == "예산 경고"]
if budget_warn:
    c2.warning("예산 경고: " + " / ".join(i.설명 for i in budget_warn))

tabs = st.tabs(["회계 현황", "사업계획서", "사용량"])
tab_acc, tab_plan = tabs[0], tabs[1]

# ================================================================ 회계 현황
with tab_acc:
    rec = cumulative_reconciliation(L, B)
    s = rec.summary
    own_total = sum(r.자체예산 or 0 for r in L.reports() if not r.취소)
    k = st.columns(3)  # 금액이 잘리지 않게 3 + 2칸
    k[0].metric("예산 배정액", lib.won(s.예산배정액))
    k[1].metric("신청 누계(재계산)", lib.won(s.재계산), f"문서 기준 {s.문서기준:,}원", delta_color="off")
    k[2].metric("잔여", lib.won(s.재계산_잔여), f"문서 기준 {s.문서기준_잔여:,}원", delta_color="off")
    k = st.columns(3)
    k[0].metric("집행률", f"{s.재계산 / s.예산배정액:.1%}" if s.예산배정액 else "-")
    k[1].metric("자체예산 합", lib.won(own_total))
    if s.차이 or rec.항목:
        label = "열린 이슈로 모두 설명됨" if rec.설명됨 else "설명 안 된 차이 있음(오류 관리 › 장부 재검사)"
        with st.expander(f"누계 두 기준 차이 {s.차이:+,}원 — {label}"):
            st.dataframe(pd.DataFrame([{"문서": i["문서번호"], "순번": i["순번"], "차이": i["차이"],
                                        "이슈": ", ".join(i["이슈"]) or "없음"} for i in rec.항목]),
                         hide_index=True, width="stretch")

    # 계획 누계 vs 신청 누계(월별)
    plan_month = [sum(p.월별[m] for p in L.plan.values()) for m in range(12)]
    net = [0] * 12
    for r in L.rows:
        if not r.취소:
            net[r.월번호 - 1] += (r.금번신청액 or 0) if r.신청서 else -(r.잔액 or 0)
    chart = pd.DataFrame({"월": [f"{m:02d}월" for m in range(1, 13)],
                          "계획 누계": pd.Series(plan_month).cumsum(), "신청 누계(실집행)": pd.Series(net).cumsum()})
    st.markdown("**계획 누계 vs 신청 누계** (월 = 사업이 속한 월)")
    st.line_chart(chart, x="월", y=["계획 누계", "신청 누계(실집행)"])

    # 장부 표
    st.markdown("**장부**")
    st.caption("잘못 만든 문서는 ⚠️ 오류 관리 › 문서 삭제에서 지울 수 있습니다(이력에 남고 되돌릴 수 있음).")
    remarks = B.ledger_remarks_detail()
    f1, f2, f3 = st.columns([2, 1, 1])
    q = f1.text_input("검색(행사명·문서번호·사업ID)", key="dash_q")
    kind = f2.selectbox("구분", ["전체", "예산신청서", "집행보고서"], key="dash_kind")
    only = f3.toggle("이슈 있는 문서만", key="dash_only")
    rows = []
    for r in L.rows:
        if kind != "전체" and r.구분 != kind:
            continue
        if only and r.순번 not in remarks:
            continue
        if q and q not in f"{r.행사명} {r.문서번호} {r.사업ID}":
            continue
        n = lambda v: "" if v is None else f"{v:,}"  # 공란은 공란 그대로(None 표시 안 함)
        rows.append({"순번": r.순번, "월": r.월, "구분": r.구분, "문서번호": r.문서번호, "행사명": r.행사명,
                     "비고": " / ".join(d for _, d in remarks.get(r.순번, [])),
                     "신청액": n(r.금번신청액), "신청누계": n(r.신청누계액), "본당": n(r.본당보조),
                     "자체": n(r.자체예산), "합계": n(r.합계), "잔액": n(r.잔액), "작성일": r.작성일,
                     "사업": r.사업ID, "상태": r.상태,
                     "_color": issue_color(remarks[r.순번][0][0]) if r.순번 in remarks else ""})
    df = pd.DataFrame(rows)
    if not df.empty:
        colors = df.pop("_color")
        styled = df.style.apply(lambda col: [f"color: #{c}" if c else "" for c in colors], subset=["비고"])
        st.dataframe(styled, hide_index=True, width="stretch", height=420,
                     column_config={"비고": st.column_config.TextColumn(width="large"),
                                    "행사명": st.column_config.TextColumn(width="medium")})
    st.caption("비고 = 열린 이슈 설명(빨강: 문서 오류, 주황: 예산 경고). 이슈가 해결되면 사라집니다.")
    with tempfile.TemporaryDirectory() as d:
        data = export_xlsx(L, B, Path(d) / "x.xlsx").read_bytes()
    st.download_button("회계내역 xlsx 다운로드(이슈·누계 시트 포함)", data,
                       file_name=f"{year}_초등부교사회_회계내역.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ================================================================ 사업계획서
with tab_plan:
    rows = []
    for p in L.plan.values():
        spent = L.business_spent(p.사업ID)
        rows.append({"사업ID": p.사업ID, "사업명": p.사업명, "시기": p.시기, "본당예산": p.본당예산, "실집행": spent,
                     "집행률": spent / p.본당예산 if p.본당예산 else 0, "남은 한도": L.business_limit(p.사업ID),
                     "연말 예상": L.year_end_projection(p.사업ID, lib.today())})
    plan_df = pd.DataFrame(rows)
    st.dataframe(plan_df.style.apply(lambda r: ["color: #C65911" if r["연말 예상"] > r["본당예산"] else ""] * len(r),
                                     axis=1),
                 hide_index=True, width="stretch",
                 column_config={"집행률": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1.2),
                                **{c: st.column_config.NumberColumn(format="%d")
                                   for c in ("본당예산", "실집행", "남은 한도", "연말 예상")}})
    st.caption("주황 = 연말 예상이 계획을 넘는 사업")
    st.markdown("**월별 배분(계획, 원)**")
    monthly = pd.DataFrame({p.사업명: p.월별 for p in L.plan.values()}, index=[f"{m:02d}월" for m in range(1, 13)])
    st.bar_chart(monthly)
    st.markdown("**개요·비고**")
    for item in lib.plan_info():
        st.markdown(f"- **{item['항목']}** {item['내용']}")
    with tempfile.TemporaryDirectory() as d:
        data = export_plan_xlsx(L, lib.plan_info(), Path(d) / "p.xlsx", lib.today()).read_bytes()
    st.download_button("사업계획서 xlsx 다운로드", data, file_name=f"{year}_사업계획서_집행현황.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ================================================================ 사용량
with tabs[2]:
    pricing = L.settings.사용량단가
    rows = load_usage(Path(ss.ws.path) / str(year) / "usage.csv")
    if not (pricing.입력_USD_1M or pricing.출력_USD_1M) or not pricing.환율_KRW_USD:
        st.info("설정 › 토큰 단가·환율을 입력하면 예상 비용이 계산됩니다(단가는 코드에 고정하지 않습니다).")
    if not rows:
        st.caption("아직 Claude 호출 기록이 없습니다.")
    else:
        summary = pd.DataFrame(summarize(rows, pricing))
        envs = st.multiselect("환경", sorted(summary["환경"].unique()), default=sorted(summary["환경"].unique()))
        view = summary[summary["환경"].isin(envs)]
        k = st.columns(4)
        proj = annual_projection(rows, pricing, "prod")
        k[0].metric("호출 수", int(view["호출"].sum()))
        k[1].metric("토큰(입력/출력)", f"{int(view['입력토큰'].sum()):,} / {int(view['출력토큰'].sum()):,}")
        k[2].metric("예상 비용(선택 환경)", lib.won(int(view["예상비용_원"].sum())))
        k[3].metric("연간 예상(운영 prod)", lib.won(proj["연간 예상(원)"]),
                    f"{proj['사용한 달']}개월 평균 {proj['월평균(원)']:,}원", delta_color="off")
        st.markdown("**월별·기능별**")
        st.dataframe(view, hide_index=True, width="stretch")
        by_month = view.groupby("월")[["예상비용_원"]].sum()
        st.bar_chart(by_month)
        buf = io.StringIO()
        view.to_csv(buf, index=False)
        st.download_button("예산 신청 근거용 사용량 CSV", ("\ufeff" + buf.getvalue()).encode("utf-8"),
                           file_name=f"{year}_Claude_사용량.csv", mime="text/csv")
