# church-budget-agent

천주교 서울대교구 개포동성당 초등부 교사회의 예산신청서(서식1)·집행보고서(서식2)를 hwpx로 자동 생성하고, 회계 장부를 갱신하며, 장부의 오류·이상 사항을 이슈로 관리하는 Streamlit 웹앱. 회계담당 1인이 운영한다.

## 운영 방식 (2026-10-23 변경)

- **운영 주체**: 회계담당 1인이 모든 기능을 쓴다. 회계담당은 매년 바뀌고 인수인계한다.
  개발자는 운영에 관여하지 않으며 개발자 전용 화면은 없다.
  새 담당자가 개발자 없이 앱만으로 한 해를 시작할 수 있어야 한다(회계연도 전환 마법사, 인수인계, docs/사용설명서.md).
  예외: Anthropic API 키 교체와 Streamlit 앱 공유 설정(초대·제거)은 개발자가 한다.
- **보안 기능 없음 (2026-10-23 결정)**: 앱 로그인·비밀번호·역할(ADMIN/VIEWER)·암호화(SETTINGS_KEY)는 모두 삭제했다.
  앱에 들어오면 모든 기능을 쓴다. 접근 제한은 코드가 아니라 **Streamlit 앱 공유 설정('초대한 사람만')**과
  **GitHub 비공개 저장소**로 한다(README 배포 절차의 필수 항목). 다시 로그인·암호화를 넣지 않는다.
- **비밀값(secrets)**: ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_REPO, DATA_BRANCH, APP_ENV(dev|prod)만.
  API 키는 앱에서 입력·저장하지 않는다. 설정 화면에는 상태만(설정됨/없음, 끝 4자리, 마지막 호출 성공 시각)
- **연락처·계좌**: 기안자·기안단체장 연락처, 은행명·계좌번호·예금주는 data/settings.json(`연락처`, `계좌`)에 평문으로 두고
  설정 화면에서 일반 입력칸으로 고친다. 신청서·보고서 생성 때 자동으로 채우고(`Settings.fill_contacts`), 문서별로 계좌를
  덮어쓸 수 있다(`계좌_override`, 문서에만 들어가고 설정·장부에는 남지 않음). 설정이 비어 있으면 문서도 빈칸.
- **검토자 부재 대비**: 모든 장부 반영 전 변경 전후 비교 + 확인 체크박스. 반영 단위로 [되돌리기](장부·이슈 함께 복원,
  되돌리기 자체도 이력에 기록, 이후 같은 칸이 바뀌었으면 거부) — `core.changes`
- **데이터는 연도별**: data/{연도}/ledger.csv·plan.csv·plan_info.csv·issues.csv·history.csv·usage.csv,
  data/settings.json(현재 회계연도 `기본정보.회계연도`, 연도별 배정액·다음 번호·마감은 `연도별`, 연락처·계좌).
  지난 연도(`연도별[연도].마감`)는 읽기 전용.
- **LLM 사용량**: 모든 호출을 data/{연도}/usage.csv에 기록(시각, 기능, 모델, 요청모델, 대체모델, 입력·출력 토큰, 소요초, APP_ENV).
  토큰 단가·환율은 설정(`사용량단가`)에서 입력하고 코드에 고정하지 않는다.

- Python 3.13(로컬 venv `budget_env/`와 Streamlit Cloud 동일), Streamlit, Claude API(모델은 `data/settings.json`의 `모델`)
- 실행: `budget_env/bin/pip install -r requirements-dev.txt`(requirements.txt는 버전 고정, dev는 + pytest), 테스트 `budget_env/bin/python -m pytest tests`
  (실제 API 스모크: `pytest tests/test_agents_live.py --live`, 단위 테스트는 tests/fakes.FakeClient로 API 대역)
- 개발자용 실행·배포·비상 복구 절차는 README.md
- 파일명은 맥 NFD일 수 있다. 경로·이름 비교는 항상 `core.paths.nfc()` / `find_child()`로 NFC 정규화한다.
- 원본 폴더 이름은 `2026년/`이다(`core.paths.source_dir()`가 `2026년`·`2026` 모두 찾음). 읽기 전용으로 다룬다.

## 폴더 구조

```
app/        Streamlit 멀티페이지 앱 (streamlit run app/Home.py)
  Home.py     st.navigation(모든 화면, 로그인 없음), 상단 회계연도·이슈 배지
  lib.py      공통: 저장소·세션 상태(연도별), persist(반영 이력 기록 + 커밋, 충돌 처리), undo, save_settings,
              LLM 사용량 기록 훅, 문서 생성, 문서별 계좌 덮어쓰기, 다운로드 안내, 장부 변화 요약, topbar
  forms.py    설정·연도 전환 공용 입력(기본 정보·연락처·계좌·교사명단, API 키 상태)
  views/      request(📝) report(🧾) post_settlement(🔁) issues(⚠️, 이력·되돌리기·재검사) dashboard(📊, 연도 선택·사용량)
              settings(⚙️) year(🗓️ 회계연도 전환 마법사) handover(📋 인수인계)
core/       장부·이슈·계산 엔진 (순수 Python, UI·LLM 의존성 없음)
  paths.py    경로 상수, NFC 헬퍼
  models.py   pydantic 모델: LineItem, RequestDoc, ReportDoc, LedgerRow, Settings, PlanItem, Issue, Resolution
  ledger.py   Ledger: load/save, next_doc_number, cumulative_requested/remaining, business_spent/limit,
              build_request/build_report/build_post_settlement → BuildResult, commit
  validate.py Finding(오류/경고), validate_request/report, scan_ledger(이슈 후보, 등록 이슈와 중복 제거)
  issues.py   IssueBook(load/save, open_issues, ledger_remarks), create_issue, commit_with_issues,
              apply_resolution — 장부 정정과 이력 기록의 유일한 경로
  export.py   장부+이슈 → 회계내역 xlsx (python -m core.export → out/)
  migrate.py  2026 엑셀 → data/ 1회 이관 (python -m core.migrate). --force는 이슈 처리·이력을 지우므로 쓰지 않는다
  secrets.py  .streamlit/secrets.toml 읽기(앱 밖 에이전트·스크립트용 API 키)
  storage.py  저장 계층: 세션별 data/ 전체 작업 사본(Workspace, 상대 경로) → commit(바뀐·새 파일). LocalStorage(내용 해시)·
              GitHubStorage(PyGithub, 파일 sha, 한 커밋, 바이너리는 blob), 받은 뒤 원본이 바뀌었으면 ConflictError.
              secrets에 GITHUB_TOKEN이 있으면 GitHub 모드(GITHUB_REPO, DATA_BRANCH 기본 "data")
  delete.py   문서 삭제(plan_delete → 미리보기, apply_delete): 앱에서 만든 문서(history '(행 추가)')만, 신청서 → 보고서 함께,
              보고서만 → 신청서 연결·상태를 만들기 전 값으로, 같은 반영의 짝(사후정산) 함께, 지운 문서만 가리키는 열린 이슈는
              해결-설명됨(문서 삭제), 뒤 신청서 누계 어긋남·빈 문서번호 경고. 원본(이관) 행은 지우지 않는다
  changes.py  반영 단위 이력(record_unit: snapshot 대비 장부 칸·행 추가/삭제·이슈 추가/필드 변경을 같은 이력ID로)과
              되돌리기(undo_unit), units(반영 목록)
  usage.py    usage.csv 기록·집계(월·기능·환경), 예상 비용·연간 예상(단가는 설정)
  plan_io.py  사업계획서 xlsx 읽기(2026 형식, 천원/원), 검증(월별 배분 합 = 본당예산, 배정액), plan.csv 쓰기
  year.py     회계연도 전환: close_year(마감), start_year(새 연도 폴더·빈 장부·이월 이슈·설정 전환)
hwpx/       hwpx 읽기·생성
  text.py     hwp/hwpx 텍스트 추출(extract_text), hwpx 표 칸 읽기(read_cells, cells_from_xml)
  layout.py   서식1·2 칸 매핑(라벨 기반 resolve) — fill·reader 공용. 빈 양식 기준 주소 REQUEST_CELLS/REPORT_CELLS
  table.py    세부내역 행 복제(expand_item_rows): rowAddr·rowCnt·표 높이·병합 rowSpan 조정, 쪽 나눔 CELL
  fill.py     문서 → hwpx(render, render_committed → out/{월}/{파일명}), 세부내역 clone/group
  reader.py   hwpx → RequestDoc/ReportDoc(read_document): 서식 판별, 읽기 실패 칸 목록
  receipts.py 영수증 첨부 docx(build_receipts_docx): 이미지/PDF, EXIF 회전, 여백 자르기, 페이지 폭 맞춤
agents/     Claude API 호출(정보 추출·문장 작성만. 계산·검증은 core)
  llm.py              공통 호출: 모델=settings `모델`, 키=ANTHROPIC_API_KEY(환경변수 → st.secrets → secrets.toml),
                      출력은 strict 도구 1개 호출로 강제(tool_choice auto — Sonnet 5.5는 강제 불가, 근거 링크는 llm.py 주석)
                      fallbacks는 settings `llm_fallbacks`("off" 기본 | "default"). 호출마다 CallInfo(실제 응답 모델·시간·토큰)
                      도구 스키마 제약(실제 호출로 확인): 속성 이름 ^[a-zA-Z0-9_.-]{1,64}$ → 한글 키는 ALIASES로 영문 변환,
                      null 허용 enum은 anyOf, null 허용(union) 속성은 스키마당 16개까지
  request_agent.py    자연어 → RequestDoc 초안(missing·defaulted·사업ID 후보)
  receipt_agent.py    영수증 이미지/PDF/HEIC → 날짜·상호·금액·품목요약(못 읽으면 null + 확인필요)
  report_agent.py     영수증 + progress_note + suggestion → ReportDoc 초안(금액은 영수증 합, 경과보고 줄은 코드가 렌더링)
  post_settlement.py  영수증 + 경과보고 → 보고서 초안 → core.build_post_settlement(신청서 역생성)
  issue_agent.py      이슈 + 사용자 설명 + 첨부 hwpx → 해결 제안(정정/설명/추가 확인 필요), 장부 복사본에 적용해 검증
  plan_agent.py       사업계획서 사진 → 사업 행(금액은 적힌 그대로 + 단위, 원 환산·검증은 코드)
scripts/    eval_extraction.py(8월 신앙학교 영수증 추출 ↔ 보고서 금액 비교, 실제 API 호출)
            check_cloud.py(secrets·GitHub 읽기/임시 브랜치 쓰기·설정(연락처·계좌 채움 여부)·Claude 1회·hwpx 왕복·xlsx
            점검 표, 비밀값·계좌·연락처 출력 없음, 운영 브랜치는 읽기만)
data/       장부와 설정(커밋 대상)
  settings.json        현재 회계연도, 기본·기안·금액·문서 기준, 연도별(배정액·다음 번호·마감), 교사명단, 모델, 사용량단가,
                       진행상태(연도 전환·인수인계), 연락처·계좌(평문 — main에서는 빈칸, 실제 값은 data 브랜치)
  {연도}/ledger.csv    문서 1건 = 1행. 비고 열 없음
  {연도}/plan.csv      사업계획(원 단위), 1월~12월 배분 / plan_info.csv 개요·비고
  {연도}/issues.csv    이슈 / history.csv 반영 단위 이력(이력ID 같은 행 = 한 반영) / usage.csv LLM 사용량
docs/       사용설명서.md(앱 '인수인계' 화면에서도 보임), 회계담당_접속안내.md(한 페이지)
.streamlit/ config.toml(테마, 업로드 한 파일 30MB — 커밋), secrets.toml.example(커밋), secrets.toml(커밋 금지)
tests/      pytest
templates/  request_template.hwpx, report_template.hwpx (빈 공식 양식)
templates/examples/  실제 작성 예시 — 커밋 금지
out/        생성 결과물 — 커밋 금지
```

### 장부 열 (data/ledger.csv)
순번, 월, 구분(예산신청서/집행보고서), 작성일, 예산배정액, 금번신청액, 신청누계액, 잔여액, 자체예산, 본당보조, 합계, 잔액, 문서번호, 행사명, 파일명, 사업ID, 연결문서번호, 상태, 양식

- 작성일·문서번호 등은 원본 표기 그대로(예: `2026년 07월 04일`, `(미기재)`). 날짜 해석은 `core.ledger.parse_date`.
- 공란은 공란 그대로 둔다(0으로 채우지 않음).
- 연결문서번호: 짝 문서의 문서번호. 짝의 문서번호가 미기재면 `#순번`.
- 상태: `정산완료` / `사후정산`(신청서·보고서 작성일 동일) / `진행중`(보고 전 신청서) / `보고서없음` / `자체집행`(본당보조 0인 단독 보고서) / `신청서없음` / `취소`(누계·검사에서 제외).
- 양식: `구`/`신`. 원본 문서에서 판별(보고서 '반납 금액' 칸, 신청서 보좌신부 결재란 → 구). 새 문서는 신.
  `python -m core.migrate --fill-form`은 이 열만 다시 채운다.
- CSV 인코딩은 `utf-8-sig`(엑셀 호환).

## 도메인 규칙

- 문서번호: 신청서 (예)와 보고서 (집)이 하나의 일련번호를 공유한다. 중복 금지.
- 신청누계액: 실집행 기준. 보고서에서 잔액(반납)이 나오면 다음 신청 누계에서 차감한다.
- 잔여액 = 예산배정액 − 신청누계액
- 보고서 금액 (신청액 = 연결 신청서 금번신청액, 근거: 순번 34 6월 교리준비비 보고서)
  - 본당보조 = min(신청액, 실사용액)
  - 자체예산 = max(0, 실사용액 − 신청액)
  - 합계(B) = 실사용액 = 본당 + 자체
  - 예산(A) = 신청액 + 자체예산
  - 잔액(A−B) = 신청액 − 본당보조 = 반납액
- 구 양식(1~3월, 4월 운영비 신청서·부활 교리 신청/보고서): 보고서는 '잔액(A−B)' 대신 '반납금액' 칸을 쓰고
  예산란에 실사용액을 적었다. 반납금액 칸 값을 잔액으로 쓰고, 보고서 금액 구조·A−B 검증에서 제외한다.
- 누계는 두 기준을 함께 보인다: 문서 기준(마지막 신청서에 적힌 누계)과 재계산 기준(신청액 합 − 반납액 합).
  차이는 열린 이슈로 설명돼야 한다(`issues.cumulative_reconciliation`, 엑셀 '누계' 시트).
- 사후정산(신청서·보고서 동시 제출)
  - 보고서를 먼저 확정하고 신청서를 역생성한다.
  - 한도 = 해당 사업 계획 본당예산 − 그 사업 실집행 누계
  - 실사용액이 한도 이하이면 신청액 = 실사용액
  - 한도를 넘으면 신청액은 한도까지만, 초과분은 보고서 자체예산
  - 문서번호는 신청서 N, 보고서 N+1, 작성일은 동일
- 교사회 운영비 = 운영비단가 × 교사수
- 월 폴더 = 사업이 속한 월(작성일 기준 아님)
- 파일명: `{월내순번}_{행사명}_{신청서|보고서}.hwpx`
- 세부내역이 양식 7행을 넘으면 settings `hwpx_row_mode`: `clone`(기본, 마지막 빈 행 복제 — 표가 한 쪽을 넘으면 다음 쪽으로)
  | `group`(같은 계정과목의 뒤쪽 항목을 "내용 외 N건"으로 묶어 7행에 맞춤, 금액 합산·단가·수량 비움, 많은 계정과목부터 필요한 만큼만)
- 계좌(은행명·계좌번호·예금주)는 칸별로 `문서.계좌_override`(빈 칸 제외) > 설정 `계좌` 순. 연락처는 설정 `연락처`.
  예금주가 문서마다 다를 수 있다(생성 화면 '이 문서만 다른 계좌 쓰기').
- 금액·누계·번호 계산은 반드시 코드가 한다. LLM은 정보 추출과 문장 작성만 한다.
  - LLM 출력에는 금액 합계를 받지 않는다: 신청서는 단가·수량만, 보고서는 영수증 번호 묶음만 받고 금액은 코드가 더한다.
  - 경과보고 한 줄 형식 "M월 D일 항목 : 금액원 / 참가자 : 이름 세례명, …"은 코드가 만든다. 참가자는 교사명단과 매칭(명단에 없으면 경고).
  - 이슈 에이전트의 변경안은 apply_resolution을 복사본에 돌려 검증한 뒤 보여 주고, 실제 반영은 사용자 확인 후.
  - 이슈 설명문의 숫자는 자리표시자({잔여액}, {누계차이}, {대상금액} 등)로만 받고 코드가 계산값으로 채운다.
    자리표시자 밖 숫자·없는 자리표시자·4문장 초과면 설명문만 1회 재요청, 그래도 남으면 숫자 문장 제외·앞 4문장.
  - 영수증: 승인번호·결제수단·가맹점명_원문도 추출. 가맹점명이 결제대행사(PG)면 대행사표기 → 보고서는 사용자 설명의
    상호(상호_사용자)를 우선. 같은 승인번호+금액+날짜는 중복 의심 경고.
  - LLM에 연락처·계좌 칸은 보내지 않는다.

## 이슈 규칙

- 장부 값은 근거 없이 바꾸지 않는다. 이상 사항은 이슈로 남긴다.
- 비고 = 열린 이슈(미해결·확인중)의 설명. 이슈가 해결되면 비고에서 사라진다(`derive_remarks`).
- 장부 변경은 문서 생성·이슈 해결·문서 삭제로만 하고, 반영마다 전후 값과 사유를 `history.csv`에 남긴다(`core.changes.record_unit`,
  앱은 `lib.persist`). 이슈는 삭제하지 않는다(단, 생성을 되돌리면 그 반영이 만든 이슈도 함께 빠진다).
- 장부 정정은 `core.issues.apply_resolution(ledger, book, 이슈ID, Resolution)`로만 한다.
  - 정정: 대상 행 값 변경(`변경`) 또는 행 추가(`추가행`, 순번·문서번호·파일명 자동, 연결 신청서 역연결). → 해결-정정
  - 설명: 값 유지, 사유 기록, `상태` 열만 변경 가능(예: 취소). → 해결-설명됨
  - `함께해결`로 같은 사유의 관련 이슈를 함께 닫는다. 복사본에서 누계·잔여를 재검증한 뒤에만 반영한다.
- 생성 검증: 오류는 생성 차단, 경고는 확인 후 진행 가능하며 진행하면 이슈로 자동 등록(`commit_with_issues`).
- `scan_ledger`는 이슈 후보만 반환한다. 등록은 사용자가 확인한 뒤 한다. 중복 판정 = 대상 순번 겹침(상태 무관) + (같은 유형 또는 이슈의 검증코드에 포함).
- 누계·잔여에 영향 없는 기재 차이는 이슈로 기록하고 '해결-설명됨'으로 닫는다. 열린 이슈는 실제 확인이 필요한 것만 남긴다.
  - 이때 이슈의 `검증코드`에 설명하는 검사 코드를 적어 두면(예: `report_sum;subsidy_vs_request`) 그 순번의 그 검사만 다시 걸리지 않는다.
- 이슈 열: 이슈ID, 대상순번, 대상문서번호, 유형, 설명, 근거, 관련이슈, 검증코드, 상태, 해결방식, 해결내용, 첨부, 처리자, 처리일
  - 유형: 계산 불일치 / 누계 누락 / 기재 오류 / 문서 누락 / 번호 중복 / 미확인 / 예산 경고 / 증빙 누락
  - 증빙 누락: 보고서 금액을 뒷받침할 영수증이 부족(예: I-026 8월 신앙학교 728,330원)
  - 예산 경고는 문서 오류가 아니라 사업계획 대비 초과(실집행 > 계획, 연말 예상 > 계획). 표시 색 주황(C65911), 문서 오류는 빨강(C00000) — `issues.issue_color`
  - 연말 예상 = 실집행 + 남은 달(매달 배분 사업이 최근 두 달 연속 신청 중이면 최근 신청액, 아니면 계획 월 배분)
  - 상태: 미해결 / 확인중 / 해결-정정 / 해결-설명됨. 검토메모: 확인 중인 내용 메모(오류 관리 화면)
  - 표시 색: 문서 오류(계산 불일치·누계 누락·기재 오류·문서 누락·번호 중복) 빨강, 예산 경고 주황, 증빙 누락·미확인 회색
  - 대상이 여럿이면 대상순번·대상문서번호·관련이슈를 `;`로 구분(예: `4;5`).

## 보안 규칙

- 영수증 이미지, `2026년/` 원본 문서, `templates/examples/`, `out/`, `.streamlit/secrets.toml`은 커밋하지 않는다(.gitignore 등록).
- 연락처·계좌 실제 값은 앱이 data 브랜치의 data/settings.json에만 커밋한다. main의 data/settings.json에서는 빈칸으로 둔다
  (tests/test_data.py가 확인). 로컬에서 값을 넣었다면 main에 커밋하지 않는다.
- 저장소는 비공개, Streamlit 앱 공유는 '초대한 사람만'. 담당자가 바뀌면 새 담당자 초대·이전 담당자 제거.
- 커밋 전 `git status`로 확인한다.
- 브랜치: 앱 코드는 `main`(Streamlit Cloud 배포 브랜치), 장부는 `data` 브랜치에 앱이 커밋한다(배포 브랜치에 push가 생기면
  앱이 재시작되므로 분리). 배포 후 장부 원본은 `data` 브랜치 — main의 data/는 배포 시점 사본이며 두 브랜치를 merge하지 않는다.
- 운영 데이터(data 브랜치, 로컬 data/2026)로 시험하지 않는다. 시험은 사본(BUDGET_DATA_DIR) 또는 임시 브랜치(check_cloud.py).

## 진행 상황

- [x] 1단계 (2026-10-23) 폴더 구조, 데이터 이관(ledger 53행, plan 15사업, 이슈 22건), settings.json, CLAUDE.md
  - 확정: 6월 신앙학교 준비비(순번 31·35) → P06, 교사명단 = 8월 하계워크숍 보고서 참가자 9명
  - I-009 해결-정정: 순번 19 작성일 공란 → '2026년 4월 25일'(원본 문서 기준, 이력 H-0001)
  - I-017(잔여액 480,000원 차이) 유지, 관련이슈 I-018. I-020은 유형 '미확인'
  - 원본 폴더의 `240726_3_7월신앙학교준비비_예산신청서.hwp`(2024년 문서), `4월/샘플_2026예산집행보고서(수정).hwpx`는 장부 대상 아님
- [x] 2단계 (2026-10-23) core 엔진: models, ledger, validate, issues, export + tests 44개
  - 누계: 문서 기준 23,216,860원 / 재계산 23,694,010원, 차이 −477,150원 = 순번 32(−1,329,705, I-013) + 37(+1,332,555, I-016·I-017) + 38(−480,000, I-017·I-018)
  - 보고서 금액 규칙 확정(위 5줄), 장부 '양식' 열 추가(구 19건: 순번 1~18, 20)
  - I-023 등록(예산 경고): P11 실집행 4,598,000 > 계획 4,320,000, 연말 예상 5,678,000
  - I-024·025(순번 21·22, 기재 오류, 해결-설명됨): 4월 전환기 보고서 본당란에 신청액 기재, 반납액 정확 → 장부 원본 유지. scan_ledger 미등록 후보 0건
  - 이력 파일 ledger_history.csv → history.csv, 다음문서번호 53
- [x] 3단계 (2026-10-23) hwpx 생성·읽기, 영수증 첨부 — 사용자 한글 확인 완료(out/샘플/ 7개, 6단계 배포 전)
  - 표 구조는 그대로 두고 칸 문단만 교체(바뀐 문단의 linesegarray 제거 → 한글이 다시 배치)
  - 칸 매핑은 라벨 기반: 작성자가 표를 고친 문서(8월 신앙캠프 보고서는 열이 합쳐짐)도 읽힘
  - 검증: 5월 어린이날 재생성이 실제 예시와 칸별 일치, 예시 4건 reader 값 = 장부, 1·7·12건 왕복 일치(tests/test_hwpx*.py)
  - 계정과목은 모든 항목이 같으면 첫 행에만 기재(예시는 칸 병합). 행사유형 칸은 표시하지 않음
  - LibreOffice는 hwpx를 열지 못해 렌더링은 한글에서 확인 필요. 영수증 docx 글꼴 기본 맑은 고딕
  - 샘플: out/샘플/ (커밋 제외)
- [x] 4단계 (2026-10-23) Claude 연동(agents/) — mock 단위 테스트 + 실제 API 스모크 3건 통과
  - anthropic SDK 2.1.0, 모델 claude-sonnet-5-5(settings), fallbacks 기본 끔(settings `llm_fallbacks`)
  - 추출 평가(신앙학교 영수증 22장): 22장 모두 금액 읽음, 확인필요 1장(티셔츠 제작비.jpg 일자 없음), 표본 4장 수동 대조 일치.
    합계 832,620원 < 보고서 1,560,950원(차이 728,330원) — 점심식사 440,000원 등 영수증이 폴더에 없는 것으로 보임
  - 8월 캠프 영수증 폴더는 없음. 신앙학교 자체예산 사용내역.xlsx는 암호화돼 정답으로 못 씀
  - 세례명만으로 여러 명이 맞으면 report_agent가 참가자_선택(후보 목록)을 돌려준다(5단계 화면에서 선택)
  - I-026 등록(증빙 누락): 순번 45·49 신앙학교 보고서, 영수증 728,330원 부족
- [x] 5단계 (2026-10-23) Streamlit 웹앱 + 운영 방식 변경(회계담당 단독·연도별 데이터·암호화 설정·되돌리기·사용량·
  회계연도 전환·인수인계). 데이터 data/ → data/2026/(값 변경 없음, history에 '대상' 열, issues에 '검토메모' 열)
  - 앱 규칙: 생성·반영 전 미리보기 + 확인 체크, 생성 뒤에는 결과만 표시(바뀐 장부로 다시 계산한 화면을 보이지 않음),
    반영 후 장부 변화(다음 문서번호·누계·잔여·해결된 이슈) 표시, 다운로드 시 '{연도}/{월} 폴더' 안내와 권장 파일명
  - 커밋 메시지: "장부: (예)2026-053 성탄 행사 신청서 추가", "이슈: I-019 해결(설명됨)", "이슈: I-019 확인중(회계담당 설명)"
  - 이슈에 검토메모 열 추가(확인 중 메모). 함께해결은 회계담당이 반영 전에 고친다
  - 예산 영향 금액(core.issues.budget_impact): 누계 누락·문서 누락=누계 단절, 계산 불일치=설명의 차이/행 어긋남,
    예산 경고=계획 초과액, 증빙 누락=설명의 부족액, 그 밖 0
  - 누계 단절(cumulative_breaks)은 자기 누계가 재계산과 같은 문서(차이 0)를 제외(앞 오류를 바로잡은 곳)
  - 이슈 해결로 뒤 문서의 누계가 새로 어긋나면 반영 전 경고(ResolutionResult.경고)
  - 테스트: tests/test_app.py(AppTest 시나리오, FakeClient), tests/test_storage.py(로컬·GitHub 대역)
- [ ] 6단계 (진행 중) Streamlit Community Cloud 배포 — 저장소 cmkkj0831-hub/church-budget-agent(비공개)
  - 문서 삭제 기능(2026-10-24, 오류 관리 › 문서 삭제 탭, core/delete.py). 커밋 메시지 "장부: (예)2026-053 성탄 행사 삭제(사유)"
  - 버전 고정(requirements.txt, Python 3.13), .streamlit/config.toml, DATA_BRANCH 기본 "data"
  - 저장소 cmkkj0831-hub/church-budget-agent(비공개)에 main·data push 완료(2026-10-23)
  - 보안 기능 전부 삭제(로그인·비밀번호·VIEWER·SETTINGS_KEY·암호화 → 앱 공유 설정으로 대체), 연락처·계좌는 settings.json 평문
  - 문서: README.md(개발자), docs/사용설명서.md(첫날 가이드·자주 하는 실수 추가), docs/회계담당_접속안내.md
