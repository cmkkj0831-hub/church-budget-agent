# church-budget-agent

천주교 서울대교구 개포동성당 초등부 교사회의 **집행보고서(서식2) hwpx와 영수증 첨부 docx를 만드는** Streamlit 웹앱.
영수증 이미지만 받아 Claude가 읽고, 경과보고·합계(B)와 설정값을 양식에 넣는다(나머지 칸은 빈칸). 회계담당 1인이 운영한다.

## 운영 방식 (2026-10-25 축소)

- **화면은 🧾 집행 보고서와 ⚙️ 설정 두 개뿐.** 예산신청서·사후정산·오류 관리(이슈)·대시보드·회계연도 전환·인수인계와
  장부(ledger)·이력·되돌리기·사용량 기록은 모두 삭제했다(git 이력에 남음). 다시 넣으려면 사용자에게 먼저 묻는다.
- **입력은 행사·목적(자연어)·일시·참가인원과 영수증 이미지 (2026-10-26)**: 보고서 화면에는 행사·목적, 일시(시작·종료 날짜·시각),
  참가인원(총, 남·여는 '기타' 펼침 안), 영수증 업로드, [보고서 만들기]만 있다. 입력값은 양식의 해당 칸에 그대로 넣고
  (여러 줄은 줄마다 문단, 박·일은 일시로 자동 계산: 박 = 종료일 − 시작일, 일 = 박 + 1), Claude가 경과보고(머리말·항목)를
  입력 내용과 올해 사업계획(data/{연도}/plan.csv의 시기·사업명·세부내역) 맥락에 맞게 쓰도록 넘긴다. hwpx에는 경과보고
  (Claude가 영수증으로 작성)·합계(B)(영수증 금액 합, 코드)와 설정값(주관단체·기안자·기안단체장·기본 장소·참가대상·연락처·계좌)만
  채우고 나머지 칸(문서번호·작성일·세부내역·본당보조·자체예산·예산(A)·잔액)은 빈 양식 그대로 둔다.
- **채운 글자는 검정**: 양식 칸 안내 글씨는 빨강·파랑 글자모양이라 그대로 이어받으면 채운 글자도 빨갛다. render가 header.xml에
  색만 검정인 글자모양 복제본을 더하고 채운 글자에만 쓴다(`hwpx.fill.black_char_props`). 양식 원래 글씨는 그대로.
  화면에서 고치는 칸도 없다(한글에서 고친다). 아무것도 저장하지 않는다(zip 내려받기만). 저장하는 것은 설정(data/settings.json)뿐.
- **보안 기능 없음 (2026-10-23 결정)**: 앱 로그인·비밀번호·역할·암호화는 없다. 접근 제한은 **Streamlit 앱 공유 설정
  ('초대한 사람만')**과 **GitHub 비공개 저장소**로 한다. 다시 로그인·암호화를 넣지 않는다.
- **비밀값(secrets)**: ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_REPO, DATA_BRANCH, APP_ENV(dev|prod)만.
  API 키는 앱에서 입력·저장하지 않는다. 설정 화면에는 상태만(설정됨/없음, 끝 4자리).
- **연락처·계좌**: 기안자·기안단체장 연락처, 은행명·계좌번호·예금주는 data/settings.json(`연락처`, `계좌`)에 평문.
  보고서 생성 때 자동으로 채운다(`Settings.fill_contacts`). 설정이 비어 있으면 문서도 빈칸.
- 개발자는 운영에 관여하지 않는다. 예외: API 키 교체, Streamlit 앱 공유 설정(초대·제거), 배포.

- Python 3.13(로컬 venv `budget_env/`와 Streamlit Cloud 동일), Streamlit, Claude API(모델은 settings `모델`)
- 실행: `budget_env/bin/pip install -r requirements-dev.txt`(requirements.txt는 버전 고정, dev는 + pytest),
  테스트 `budget_env/bin/python -m pytest tests`
  (실제 API 스모크: `pytest tests/test_agents_live.py --live`, 단위 테스트는 tests/fakes.FakeClient로 API 대역)
- 개발자용 실행·배포·비상 복구 절차는 README.md
- 파일명은 맥 NFD일 수 있다. 경로·이름 비교는 항상 `core.paths.nfc()` / `find_child()`로 NFC 정규화한다.
- 원본 폴더 이름은 `2026년/`이다(`core.paths.source_dir()`가 `2026년`·`2026` 모두 찾음). 읽기 전용으로 다룬다.

## 폴더 구조

```
app/        Streamlit 앱 (streamlit run app/Home.py)
  Home.py     st.navigation(집행 보고서·설정, 로그인 없음)
  lib.py      공통: 저장소·설정·사업계획 상태, save_settings(커밋, 충돌 처리), 문서 생성(설정의 연락처·계좌 채움), 업로드,
              일시·참가인원 입력(event_fields·event_values)
  forms.py    설정 입력(기본 정보·회계연도·기안자·연락처·계좌·교사명단), API 키 상태
  views/      report(🧾) settings(⚙️)
core/       순수 Python(UI·LLM 의존 없음)
  paths.py    경로 상수, NFC 헬퍼
  models.py   pydantic: LineItem, AccountInfo, DocBase, RequestDoc(hwpx 모듈 호환용), ReportDoc, Settings, Receipt
  report.py   load_settings/save_settings, load_plan(사업계획 시기·사업명·세부내역), nights_days(박·일), receipts_total,
              build_report(설정 + 경과보고 + 합계 + 행사명·목적·일시·인원 → 그 칸만 채운 ReportDoc),
              file_stem(집행보고서_{가장 이른 영수증 날짜})
  secrets.py  .streamlit/secrets.toml 읽기(앱 밖 에이전트·스크립트용 API 키)
  storage.py  저장 계층: 세션별 data/ 작업 사본(Workspace) → commit(바뀐·새 파일). LocalStorage·GitHubStorage(PyGithub,
              한 커밋, 바이너리는 blob), 받은 뒤 원본이 바뀌었으면 ConflictError. GITHUB_TOKEN이 있으면 GitHub 모드(DATA_BRANCH 기본 "data")
hwpx/       hwpx 읽기·생성 (신청서·보고서 두 양식 모두 다룸 — 앱은 보고서만 씀)
  text.py     hwp/hwpx 텍스트 추출, hwpx 표 칸 읽기
  layout.py   서식1·2 칸 매핑(라벨 기반 resolve)
  table.py    세부내역 행 복제(expand_item_rows)
  fill.py     문서 → hwpx(render), 세부내역 clone/group
  reader.py   hwpx → RequestDoc/ReportDoc(read_document)
  receipts.py 영수증 첨부 docx(build_receipts_docx): 이미지/PDF/HEIC, EXIF 회전, 여백 자르기, 페이지 폭 맞춤
agents/     Claude API 호출(정보 추출·문장 작성만. 계산은 core)
  llm.py              공통 호출 call(settings, …): 모델=settings `모델`, 키=ANTHROPIC_API_KEY(환경변수 → st.secrets → secrets.toml),
                      출력은 strict 도구 1개 호출로 강제(tool_choice auto — Sonnet 5.5는 강제 불가),
                      fallbacks는 settings `llm_fallbacks`("off" 기본 | "default"). 호출마다 CallInfo
                      도구 스키마 제약: 속성 이름 ^[a-zA-Z0-9_.-]{1,64}$ → 한글 키는 ALIASES로 영문 변환,
                      null 허용 enum은 anyOf, null 허용(union) 속성은 스키마당 16개까지
  receipt_agent.py    영수증 이미지/PDF/HEIC → 날짜·상호·금액·품목요약·승인번호(못 읽으면 null + 확인필요)
  report_agent.py     행사 정보 + 사업계획 + 영수증 → 세부내역 묶음·경과보고 줄(compose_report), 고른 사업(사업명_참고, 화면에 표시)
scripts/    check_cloud.py(secrets·GitHub 읽기/임시 브랜치 쓰기·설정·Claude 1회·보고서 hwpx 왕복·영수증 docx, 비밀값 출력 없음),
            eval_extraction.py(8월 신앙학교 영수증 추출 평가, 실제 API 호출)
data/       settings.json(설정 — main에서는 연락처·계좌 빈칸, 실제 값은 data 브랜치)
  {연도}/plan.csv  사업계획(2026_사업계획서 엑셀에서 옮김). 경과보고 맥락용으로 시기·사업명·세부내역만 쓴다
docs/       사용설명서.md, 회계담당_접속안내.md
.streamlit/ config.toml(테마, 업로드 한 파일 30MB — 커밋), secrets.toml.example(커밋), secrets.toml(커밋 금지)
tests/      pytest
templates/  request_template.hwpx, report_template.hwpx (빈 공식 양식)
templates/examples/  실제 작성 예시 — 커밋 금지
out/        생성 결과물 — 커밋 금지
```

## 도메인 규칙

- 합계(B) = 영수증 금액 합(읽지 못한 금액은 0, 화면에 경고). 세부내역·본당보조·자체예산·예산(A)·잔액은 채우지 않는다.
- 파일명: `집행보고서_{YYYYMMDD}.hwpx`, `…_영수증첨부.docx`, zip `…zip`(날짜 = 가장 이른 영수증 날짜, 없으면 오늘).
- 예전 금액 규칙(참고, 지금은 쓰지 않음): 본당 = min(신청, 실사용), 자체 = max(0, 실사용 − 신청), 예산A = 신청 + 자체, 잔액 = 신청 − 본당.
- hwpx 모듈은 세부내역 행 복제(clone)·묶기(group)도 지원하지만 지금 앱은 세부내역을 채우지 않는다.
- 금액 계산은 반드시 코드가 한다. LLM은 정보 추출과 문장 작성만 한다.
  - LLM 출력에는 금액을 받지 않는다: 영수증 번호 묶음만 받고 금액은 코드가 더한다.
  - 경과보고 한 줄 형식 "M월 D일 항목 : 금액원"은 코드가 만든다(메모 입력이 없으므로 참가자는 비어 있다).
  - 영수증: 승인번호·결제수단·가맹점명_원문도 추출. 가맹점명이 결제대행사(PG)면 대행사표기 → 사용자 설명의 상호 우선.
    같은 승인번호+금액+날짜는 중복 의심 경고.
  - LLM에 연락처·계좌 칸은 보내지 않는다.
- 건의사항(차기 계획) 입력칸은 없다(보고서 건의사항은 빈칸).

## 보안 규칙

- 영수증 이미지, `2026년/` 원본 문서, `templates/examples/`, `out/`, `.streamlit/secrets.toml`은 커밋하지 않는다(.gitignore 등록).
- 연락처·계좌 실제 값은 앱이 data 브랜치의 data/settings.json에만 커밋한다. main의 data/settings.json에서는 빈칸으로 둔다.
  로컬에서 값을 넣었다면 main에 커밋하지 않는다.
- 저장소는 비공개, Streamlit 앱 공유는 '초대한 사람만'. 담당자가 바뀌면 새 담당자 초대·이전 담당자 제거.
- 커밋 전 `git status`로 확인한다.
- 브랜치: 앱 코드는 `main`(Streamlit Cloud 배포 브랜치), 설정은 `data` 브랜치에 앱이 커밋한다(배포 브랜치에 push가 생기면
  앱이 재시작되므로 분리). 두 브랜치를 merge하지 않는다.
- 운영 데이터(data 브랜치)로 시험하지 않는다. 시험은 사본(BUDGET_DATA_DIR) 또는 임시 브랜치(check_cloud.py).

## 진행 상황

- [x] 1~5단계 (2026-10-23) 데이터 이관·장부 엔진·hwpx 생성/읽기·Claude 연동·Streamlit 앱(신청서·보고서·사후정산·이슈·대시보드 등).
  hwpx는 사용자가 한글로 확인 완료(out/샘플/). 예전 장부(data/2026: 53행, 이슈 26건)는 git 이력과 data 브랜치에 남아 있다.
- [x] 6단계 (2026-10-23~25) Streamlit Community Cloud 배포 — 저장소 cmkkj0831-hub/church-budget-agent(비공개), main·data 브랜치
  - 버전 고정(requirements.txt, Python 3.13), .streamlit/config.toml, DATA_BRANCH "data"
  - 보안 기능 삭제(앱 공유 설정으로 대체), 연락처·계좌는 settings.json 평문
- [x] 7단계 (2026-10-25) 집행보고서·설정만 남기고 축소(장부 없이 문서번호·연결 신청서·신청액 직접 입력, 박·일 칸 삭제)
- [x] 8단계 (2026-10-26) 보고서 입력을 영수증 이미지 하나로: 경과보고·합계(B)·설정값만 채우고 나머지 빈칸, 설정 '문서 양식' 탭 삭제
  - 행사·목적 입력칸 추가(경과보고 맥락 + 양식의 행사명·목적 칸에 그대로)
  - 채운 글자 검정, 사업계획(시기·사업명) 맥락 반영, 일시(박·일 자동)·참가인원(남·여는 기타) 입력 다시 추가
