# church-budget-agent

천주교 서울대교구 개포동성당 초등부 교사회의 **집행보고서(서식2) hwpx와 영수증 첨부 docx를 만드는** Streamlit 웹앱.
영수증 이미지를 받아 Claude가 읽고, 세부내역·경과보고를 채워 양식에 넣는다. 회계담당 1인이 운영한다.

## 운영 방식 (2026-10-25 축소)

- **화면은 🧾 집행 보고서와 ⚙️ 설정 두 개뿐.** 예산신청서·사후정산·오류 관리(이슈)·대시보드·회계연도 전환·인수인계와
  장부(ledger)·이력·되돌리기·사용량 기록은 모두 삭제했다(git 이력에 남음). 다시 넣으려면 사용자에게 먼저 묻는다.
- **장부 없음**: 보고서 문서번호·연결 신청서 문서번호·신청액(연결 신청서 금번신청액)은 회계담당이 직접 입력한다.
  보고서를 만들어도 아무것도 저장하지 않는다(hwpx + 영수증 docx zip 내려받기만). 저장하는 것은 설정(data/settings.json)뿐.
- **보안 기능 없음 (2026-10-23 결정)**: 앱 로그인·비밀번호·역할·암호화는 없다. 접근 제한은 **Streamlit 앱 공유 설정
  ('초대한 사람만')**과 **GitHub 비공개 저장소**로 한다. 다시 로그인·암호화를 넣지 않는다.
- **비밀값(secrets)**: ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_REPO, DATA_BRANCH, APP_ENV(dev|prod)만.
  API 키는 앱에서 입력·저장하지 않는다. 설정 화면에는 상태만(설정됨/없음, 끝 4자리).
- **연락처·계좌**: 기안자·기안단체장 연락처, 은행명·계좌번호·예금주는 data/settings.json(`연락처`, `계좌`)에 평문.
  보고서 생성 때 자동으로 채우고(`Settings.fill_contacts`), 문서별로 계좌를 덮어쓸 수 있다(`계좌_override`, 문서에만).
  설정이 비어 있으면 문서도 빈칸.
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
  lib.py      공통: 저장소·설정 상태, save_settings(커밋, 충돌 처리), 문서 생성(설정의 연락처·계좌 채움), 업로드,
              행사 정보 입력(event_fields·event_values: 일시·장소·참가대상·인원 — 박·일 칸 없음), 문서별 계좌 덮어쓰기
  forms.py    설정 입력(기본 정보·회계연도·기안자·연락처·계좌·교사명단), API 키 상태
  views/      report(🧾) settings(⚙️)
core/       순수 Python(UI·LLM 의존 없음)
  paths.py    경로 상수, NFC 헬퍼
  models.py   pydantic: LineItem, AccountInfo, DocBase, RequestDoc(hwpx 모듈 호환용), ReportDoc, Settings, Receipt
  report.py   load_settings/save_settings, report_amounts, build_report(초안 + 설정 + 신청액 → 금액·기본값·오류/경고),
              file_name, doc_number_prefix
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
  report_agent.py     행사 정보 + 영수증 + 경과보고 메모 → 세부내역 묶음·경과보고 줄(compose_report)
scripts/    check_cloud.py(secrets·GitHub 읽기/임시 브랜치 쓰기·설정·Claude 1회·보고서 hwpx 왕복·영수증 docx, 비밀값 출력 없음),
            eval_extraction.py(8월 신앙학교 영수증 추출 평가, 실제 API 호출)
data/       settings.json(설정 — main에서는 연락처·계좌 빈칸, 실제 값은 data 브랜치)
docs/       사용설명서.md, 회계담당_접속안내.md
.streamlit/ config.toml(테마, 업로드 한 파일 30MB — 커밋), secrets.toml.example(커밋), secrets.toml(커밋 금지)
tests/      pytest
templates/  request_template.hwpx, report_template.hwpx (빈 공식 양식)
templates/examples/  실제 작성 예시 — 커밋 금지
out/        생성 결과물 — 커밋 금지
```

## 도메인 규칙

- 보고서 금액(신청액 = 회계담당이 입력한 연결 신청서 금번신청액, 근거: 예전 장부 순번 34 6월 교리준비비 보고서)
  - 본당보조 = min(신청액, 실사용액)
  - 자체예산 = max(0, 실사용액 − 신청액)
  - 합계(B) = 실사용액 = 본당 + 자체
  - 예산(A) = 신청액 + 자체예산
  - 잔액(A−B) = 신청액 − 본당보조 = 반납액
- build_report 기본값: 작성일 = 오늘, 주관단체·기안자·기안단체장 = 설정, 장소·참가대상 = 설정 기본값(비웠을 때),
  행사 시각을 비우면 설정 기본 시각, 종료일을 비우면 시작일. 행사 날짜를 비우면 영수증을 읽은 뒤 영수증 날짜로 채운다(화면).
- 오류(생성 막음): 행사명 없음, 세부내역 없음, 신청액 음수, group 모드인데 계정과목이 7종 초과.
  경고: 문서번호·연결 신청서 번호 비어 있음, 신청액 0, 일시 비어 있음, 작성일 연도 ≠ 회계연도.
- 파일명: `{행사명}_보고서.hwpx`, `{행사명}_보고서_영수증첨부.docx`, zip `{행사명}_보고서.zip`. 저장 안내 폴더 `{회계연도}/{월}월`.
- 문서번호 칸에는 설정 `금액문서기준.보고서번호접두`(예: `(집){연도}-`)가 미리 들어간다.
- 세부내역이 양식 7행을 넘으면 settings `hwpx_row_mode`: `clone`(기본, 행 복제) | `group`(같은 계정과목의 뒤쪽 항목을 "내용 외 N건"으로 묶음)
- 금액 계산은 반드시 코드가 한다. LLM은 정보 추출과 문장 작성만 한다.
  - LLM 출력에는 금액을 받지 않는다: 보고서는 영수증 번호 묶음만 받고 금액은 코드가 더한다.
  - 경과보고 한 줄 형식 "M월 D일 항목 : 금액원 / 참가자 : 이름 세례명, …"은 코드가 만든다. 참가자는 교사명단과 매칭
    (명단에 없으면 경고, 세례명이 겹치면 화면에서 고르게 함).
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
