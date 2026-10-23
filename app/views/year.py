"""🗓️ 회계연도 전환(회계담당): 단계형 마법사. 진행 상태는 settings.json `진행상태.연도전환`에 저장해 이어서 할 수 있다.
① 지난해 마감 ② 미해결 이슈 이월 ③ 새 사업계획서 ④ 배정액·새 장부 ⑤ 기본 정보·연락처·계좌 ⑥ API 키·앱 공유 확인."""
import tempfile
from pathlib import Path

import forms
import lib
import pandas as pd
import streamlit as st

from agents.llm import AgentError
from agents.plan_agent import extract_plan
from core.export import export_annual_xlsx
from core.issues import IssueBook
from core.ledger import BlockedError, Ledger, load_settings
from core.models import Settings
from core.plan_io import MONTHS, PLAN_COLUMNS, parse_plan_xlsx, plan_problems
from core.usage import load_usage
from core.year import start_year

ss = st.session_state
ws = Path(ss.ws.path)
settings = load_settings(ws)
state = settings.진행상태.get("연도전환") or {}
st.title("🗓️ 회계연도 전환")

STEPS = ["지난해 마감", "미해결 이슈 이월", "새 사업계획서", "배정액·새 장부", "기본 정보·연락처", "API 키·앱 공유"]


def save_state(new_state: dict | None, message: str, settings_obj: Settings | None = None) -> None:
    s = settings_obj or load_settings(ws)
    if new_state is None:
        s.진행상태.pop("연도전환", None)
    else:
        s.진행상태["연도전환"] = new_state
    if lib.save_settings(message, s):
        st.rerun()


if not state:
    cur = settings.현재연도
    st.markdown(f"현재 회계연도는 **{cur}년**입니다. 새 회계연도를 시작하면 {cur}년은 읽기 전용이 되고, "
                "새 장부는 문서번호 001부터, 누계 0으로 시작합니다.")
    new_year = st.number_input("새 회계연도", cur + 1, cur + 5, cur + 1)
    if st.button("전환 시작", type="primary"):
        save_state({"이전연도": cur, "대상연도": int(new_year), "단계": 1}, f"연도전환: {new_year} 시작")
    st.stop()

prev, new, step = state["이전연도"], state["대상연도"], state["단계"]
st.progress((step - 1) / len(STEPS), text=f"{prev} → {new} · {step}/{len(STEPS)} {STEPS[step - 1]}")
st.caption(" → ".join(f"{'✅' if n < step else ('▶️' if n == step else '⬜')} {n}.{name}"
                      for n, name in enumerate(STEPS, 1)))
if step < 4 and st.button("전환 취소(새 장부를 만들기 전이라 되돌릴 수 있음)"):
    s = load_settings(ws)
    s.year_info(prev).마감 = False
    save_state(None, f"연도전환: {new} 취소({prev} 마감 해제)", s)

# ---------------------------------------------------------------- ① 지난해 마감
if step == 1:
    st.subheader(f"① {prev}년 마감")
    L, B = Ledger.load(ws, prev), IssueBook.load(ws, prev)
    info_path = ws / str(prev) / "plan_info.csv"
    from core.ledger import read_csv
    info = read_csv(info_path) if info_path.exists() else []
    with tempfile.TemporaryDirectory() as d:
        data = export_annual_xlsx(L, B, info, load_usage(ws / str(prev) / "usage.csv"),
                                  Path(d) / "a.xlsx", lib.today()).read_bytes()
    st.download_button(f"{prev}년 연간 결산 xlsx(장부·이슈·누계·사업별 집행·사용량)", data,
                       file_name=f"{prev}_연간결산.xlsx", type="primary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption(f"📁 {prev} 폴더에 보관하세요. 미해결 이슈 {len(B.open_issues())}건 · 장부 {len(L.rows)}행")
    ok = st.checkbox("결산 파일을 받았고, 이제 이 연도를 읽기 전용으로 바꿉니다", key="yr_close_ok")
    if st.button("마감하고 다음", disabled=not ok, type="primary"):
        s = load_settings(ws)
        s.year_info(prev).마감 = True
        save_state(state | {"단계": 2}, f"연도전환: {prev} 마감(읽기 전용)", s)

# ---------------------------------------------------------------- ② 이월
elif step == 2:
    st.subheader("② 미해결 이슈 이월")
    B = IssueBook.load(ws, prev)
    open_issues = B.open_issues()
    if not open_issues:
        st.success("미해결 이슈가 없습니다.")
    df = pd.DataFrame([{"이월": i.이슈ID in state.get("이월", []), "이슈ID": i.이슈ID, "유형": i.유형,
                        "설명": i.설명} for i in open_issues])
    picked = st.data_editor(df, hide_index=True, width="stretch", key="yr_carry",
                            disabled=["이슈ID", "유형", "설명"]) if open_issues else df
    st.caption("이월한 이슈는 새 연도 이슈 목록에 '[이월]'로 들어갑니다. 이월하지 않은 이슈는 지난 연도에 미해결로 남습니다.")
    chosen = [r["이슈ID"] for r in picked.to_dict("records") if r["이월"]] if open_issues else []
    c = st.columns(2)
    if c[0].button("이전"):
        save_state(state | {"단계": 1}, "연도전환: 이전 단계")
    if c[1].button(f"{len(chosen)}건 이월하고 다음", type="primary"):
        save_state(state | {"단계": 3, "이월": chosen}, f"연도전환: 이월 {len(chosen)}건 선택")

# ---------------------------------------------------------------- ③ 사업계획서
elif step == 3:
    st.subheader(f"③ {new}년 사업계획서")
    how = st.radio("입력 방법", ["사업계획서 엑셀(xlsx)", "사업계획서 사진(Claude가 읽기)", "지난해 계획 복사 후 고치기"],
                   horizontal=True, key="yr_how")
    if how.startswith("사업계획서 엑셀"):
        up = st.file_uploader("2026 사업계획서와 같은 형식의 xlsx", type=["xlsx"], key="yr_xlsx")
        if up is not None and st.button("엑셀 읽기"):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "plan.xlsx"
                p.write_bytes(up.getvalue())
                ss.yr_plan, ss.yr_info = parse_plan_xlsx(p)
                ss.yr_notes = []
    elif how.startswith("사업계획서 사진"):
        lib.uploader("사업계획서 사진(여러 장)", "yr_photos", ["jpg", "jpeg", "png", "heic", "pdf"])
        if ss.get("yr_photos") and st.button("사진 읽기(Claude)"):
            with st.spinner("사업계획서를 읽는 중…"):
                try:
                    ext = extract_plan(lib.save_uploads("yr_photos"), lib.ledger(), client=lib.llm_client())
                    ss.yr_plan, ss.yr_info, ss.yr_notes = ext.plan, ext.info, ext.notes
                except AgentError as e:
                    st.error(str(e))
    else:
        if st.button("지난해 계획 불러오기"):
            from core.ledger import read_csv
            ss.yr_plan = read_csv(ws / str(prev) / "plan.csv")
            info_path = ws / str(prev) / "plan_info.csv"
            ss.yr_info = read_csv(info_path) if info_path.exists() else []
            ss.yr_notes = ["지난해 계획을 복사했습니다. 올해 금액으로 고치세요."]
    if "yr_plan" not in ss and state.get("사업계획"):
        ss.yr_plan, ss.yr_info, ss.yr_notes = state["사업계획"], state.get("개요", []), []
    if ss.get("yr_plan"):
        for n in ss.get("yr_notes") or []:
            st.warning(n)
        st.markdown("**검토·수정** (금액은 원 단위)")
        df = pd.DataFrame(ss.yr_plan, columns=PLAN_COLUMNS)
        for col in ["자체예산", "본당예산", *MONTHS]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        edited = st.data_editor(df, num_rows="dynamic", hide_index=True, width="stretch", key="yr_plan_editor")
        plan = []
        for r in edited.to_dict("records"):
            if not str(r.get("사업ID") or "").strip():
                continue
            prev_amount = r.get("전년도집행액")
            r["전년도집행액"] = "" if prev_amount in (None, "") or pd.isna(prev_amount) else int(float(prev_amount))
            plan.append({k: (int(r[k]) if k in ["자체예산", "본당예산", *MONTHS] else ("" if r[k] is None else r[k]))
                         for k in PLAN_COLUMNS})
        total = sum(p["본당예산"] for p in plan)
        monthly = [sum(p[m] for p in plan) for m in MONTHS]
        c = st.columns(2)
        c[0].metric("본당예산 합계", lib.won(total))
        c[1].metric("월별 배분 합계", lib.won(sum(monthly)))
        st.bar_chart(pd.DataFrame({"월별 배분": monthly}, index=MONTHS))
        info_df = st.data_editor(pd.DataFrame(ss.get("yr_info") or [], columns=["항목", "내용"]), num_rows="dynamic",
                                 hide_index=True, width="stretch", key="yr_info_editor")
        problems = plan_problems(plan)
        for p in problems:
            st.error(p)
        if not problems:
            st.success("합계·월별 배분 검증 통과")
        c = st.columns(2)
        if c[0].button("이전"):
            save_state(state | {"단계": 2}, "연도전환: 이전 단계")
        if c[1].button("사업계획서 확정", type="primary", disabled=bool(problems)):
            info = [r for r in info_df.to_dict("records") if r.get("항목")]
            save_state(state | {"단계": 4, "사업계획": plan, "개요": info}, f"연도전환: {new} 사업계획서 확정")

# ---------------------------------------------------------------- ④ 새 장부
elif step == 4:
    st.subheader(f"④ {new}년 배정액 확정과 새 장부 생성")
    plan, info = state["사업계획"], state.get("개요", [])
    total = sum(int(p["본당예산"]) for p in plan)
    allocation = st.number_input("본당 예산 배정액", 0, value=int(state.get("배정액") or total), step=10000)
    if allocation != total:
        st.warning(f"사업계획서 본당예산 합계({total:,}원)와 다릅니다. 본당에서 배정한 금액이 맞는지 확인하세요.")
    carry = [i for i in IssueBook.load(ws, prev).issues if i.이슈ID in state.get("이월", [])]
    st.table(pd.DataFrame({"회계연도": f"{new}년", "배정액": lib.won(allocation), "사업 수": f"{len(plan)}개",
                           "첫 문서번호": "001", "신청누계": "0원", "이월 이슈": f"{len(carry)}건"}.items(),
                          columns=["항목", "값"]).set_index("항목"))
    ok = st.checkbox(f"{new}년 장부를 새로 만듭니다(현재 회계연도가 {new}년으로 바뀝니다)", key="yr_create_ok")
    c = st.columns(2)
    if c[0].button("이전"):
        save_state(state | {"단계": 3}, "연도전환: 이전 단계")
    if c[1].button("새 장부 생성", type="primary", disabled=not ok):
        try:
            s = start_year(ws, new, plan, info, int(allocation), carry, prev)
        except BlockedError as e:
            st.error(str(e))
            st.stop()
        ss.year = new
        save_state(state | {"단계": 5, "배정액": int(allocation)}, f"회계연도: {new} 시작(배정액 {allocation:,}원)", s)

# ---------------------------------------------------------------- ⑤ 기본 정보
elif step == 5:
    st.subheader("⑤ 기본 정보·기안자·연락처·계좌·교사명단 갱신")
    s = load_settings(ws)
    data = forms.basic_form(s, "yr")
    if data:
        new_settings = Settings(**data)
        new_settings.진행상태["연도전환"] = state | {"단계": 6}
        if lib.save_settings(f"연도전환: {new} 기본 정보 갱신", new_settings):
            st.rerun()
    if st.button("바꿀 것 없음 — 다음"):
        save_state(state | {"단계": 6}, "연도전환: 기본 정보 유지")

# ---------------------------------------------------------------- ⑥ API 키·앱 공유
elif step == 6:
    st.subheader("⑥ API 키와 앱 공유 확인")
    forms.api_key_status()
    st.markdown("담당자가 바뀌었다면 Streamlit 앱 공유 설정(Settings › Sharing)에서 **새 담당자 이메일을 초대하고 "
                "이전 담당자를 제거**하세요(개발자에게 요청). 이 앱에는 계좌·연락처가 저장되어 있습니다.")
    if st.button("회계연도 전환 마치기", type="primary"):
        save_state(None, f"연도전환: {new} 완료")
