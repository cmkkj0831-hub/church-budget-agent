# church-budget-agent

천주교 서울대교구 개포동성당 초등부 교사회의 예산신청서(서식1)·집행보고서(서식2)를 hwpx로 자동 생성하고, 회계 장부를 갱신하며, 장부의 오류·이상 사항을 이슈로 관리하고, 회계관리자가 Streamlit 대시보드로 확인하는 시스템.

- Python 3.11+ (로컬 venv `budget_env/`는 3.13), Streamlit, Claude API(모델은 `data/settings.json`의 `모델`)
- 실행: `budget_env/bin/pip install -r requirements.txt`, 테스트 `budget_env/bin/python -m pytest tests`
- 파일명은 맥 NFD일 수 있다. 경로·이름 비교는 항상 `core.paths.nfc()` / `find_child()`로 NFC 정규화한다.
- 원본 폴더 이름은 `2026년/`이다(`core.paths.source_dir()`가 `2026년`·`2026` 모두 찾음). 읽기 전용으로 다룬다.

## 폴더 구조

```
app/        Streamlit 웹앱(대시보드)
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
hwpx/       hwpx 템플릿 채우기·생성
agents/     Claude API 호출(정보 추출·문장 작성)
data/       장부와 설정(커밋 대상)
  ledger.csv         문서 1건 = 1행. 비고 열 없음
  plan.csv           사업계획(원 단위), 사업ID P01~P15, 1월~12월 배분
  plan_info.csv      사업계획서 개요·비고
  issues.csv         이슈
  history.csv        장부 변경 이력(이슈 해결로만 생김)
  settings.json      기본·기안·금액·문서 기준, 교사명단, 모델 (민감값 없음)
docs/       문서
tests/      pytest
templates/  request_template.hwpx, report_template.hwpx (빈 공식 양식)
templates/examples/  실제 작성 예시 — 커밋 금지
out/        생성 결과물 — 커밋 금지
```

### 장부 열 (data/ledger.csv)
순번, 월, 구분(예산신청서/집행보고서), 작성일, 예산배정액, 금번신청액, 신청누계액, 잔여액, 자체예산, 본당보조, 합계, 잔액, 문서번호, 행사명, 파일명, 사업ID, 연결문서번호, 상태

- 작성일·문서번호 등은 원본 표기 그대로(예: `2026년 07월 04일`, `(미기재)`). 날짜 해석은 `core.ledger.parse_date`.
- 공란은 공란 그대로 둔다(0으로 채우지 않음).
- 연결문서번호: 짝 문서의 문서번호. 짝의 문서번호가 미기재면 `#순번`.
- 상태: `정산완료` / `사후정산`(신청서·보고서 작성일 동일) / `진행중`(보고 전 신청서) / `보고서없음` / `자체집행`(본당보조 0인 단독 보고서) / `신청서없음` / `취소`(누계·검사에서 제외).
- CSV 인코딩은 `utf-8-sig`(엑셀 호환).

## 도메인 규칙

- 문서번호: 신청서 (예)와 보고서 (집)이 하나의 일련번호를 공유한다. 중복 금지.
- 신청누계액: 실집행 기준. 보고서에서 잔액(반납)이 나오면 다음 신청 누계에서 차감한다.
- 잔여액 = 예산배정액 − 신청누계액
- 보고서 금액
  - 초과 지출분은 자체예산, 본당보조는 신청액 그대로
  - 미사용분은 잔액(반납)
  - 본당보조 + 자체예산 = 합계(B)
  - 양식 기준 일반식: 예산(A) = 본당보조 + 자체예산, 잔액 = A − 합계(B). 잔액 0이면 위 식과 같고,
    반납이 있으면 본당보조 = 신청액, 자체 = 0, A − B = 반납. (올해 1월 운영비·6월 교리 보고서는 본당=실사용으로 적어 이 식과 다름)
- 사후정산(신청서·보고서 동시 제출)
  - 보고서를 먼저 확정하고 신청서를 역생성한다.
  - 한도 = 해당 사업 계획 본당예산 − 그 사업 실집행 누계
  - 실사용액이 한도 이하이면 신청액 = 실사용액
  - 한도를 넘으면 신청액은 한도까지만, 초과분은 보고서 자체예산
  - 문서번호는 신청서 N, 보고서 N+1, 작성일은 동일
- 교사회 운영비 = 운영비단가 × 교사수
- 월 폴더 = 사업이 속한 월(작성일 기준 아님)
- 파일명: `{월내순번}_{행사명}_{신청서|보고서}.hwpx`
- 금액·누계·번호 계산은 반드시 코드가 한다. LLM은 정보 추출과 문장 작성만 한다.

## 이슈 규칙

- 장부 값은 근거 없이 바꾸지 않는다. 이상 사항은 이슈로 남긴다.
- 비고 = 열린 이슈(미해결·확인중)의 설명. 이슈가 해결되면 비고에서 사라진다(`derive_remarks`).
- 장부 변경은 이슈 해결을 통해서만 하고, 전후 값과 사유를 `history.csv`에 남긴다. 이슈는 삭제하지 않는다.
- 장부 정정은 `core.issues.apply_resolution(ledger, book, 이슈ID, Resolution)`로만 한다.
  - 정정: 대상 행 값 변경(`변경`) 또는 행 추가(`추가행`, 순번·문서번호·파일명 자동, 연결 신청서 역연결). → 해결-정정
  - 설명: 값 유지, 사유 기록, `상태` 열만 변경 가능(예: 취소). → 해결-설명됨
  - `함께해결`로 같은 사유의 관련 이슈를 함께 닫는다. 복사본에서 누계·잔여를 재검증한 뒤에만 반영한다.
- 생성 검증: 오류는 생성 차단, 경고는 확인 후 진행 가능하며 진행하면 이슈로 자동 등록(`commit_with_issues`).
- `scan_ledger`는 이슈 후보만 반환한다. 등록은 사용자가 확인한 뒤 한다. 중복 판정 = 같은 유형 + 대상 순번 겹침(상태 무관).
- 이슈 열: 이슈ID, 대상순번, 대상문서번호, 유형, 설명, 근거, 관련이슈, 상태, 해결방식, 해결내용, 첨부, 처리자, 처리일
  - 유형: 계산 불일치 / 누계 누락 / 기재 오류 / 문서 누락 / 번호 중복 / 미확인
  - 상태: 미해결 / 확인중 / 해결-정정 / 해결-설명됨
  - 대상이 여럿이면 대상순번·대상문서번호·관련이슈를 `;`로 구분(예: `4;5`).

## 보안 규칙

- 연락처·계좌번호, 영수증 이미지, `2026년/` 원본 문서, `templates/examples/`, `out/`, `.streamlit/secrets.toml`은 커밋하지 않는다(.gitignore 등록).
- 민감값(연락처, 은행명, 계좌번호, 예금주)은 settings.json이 아니라 `.streamlit/secrets.toml`에서 읽는다(`secrets.toml.example` 참고).
- 커밋 전 `git status`로 확인한다.

## 진행 상황

- [x] 1단계 (2026-10-23) 폴더 구조, 데이터 이관(ledger 53행, plan 15사업, 이슈 22건), settings.json, CLAUDE.md
  - 확정: 6월 신앙학교 준비비(순번 31·35) → P06, 교사명단 = 8월 하계워크숍 보고서 참가자 9명
  - I-009 해결-정정: 순번 19 작성일 공란 → '2026년 4월 25일'(원본 문서 기준, 이력 H-0001)
  - I-017(잔여액 480,000원 차이) 유지, 관련이슈 I-018. I-020은 유형 '미확인'
  - 원본 폴더의 `240726_3_7월신앙학교준비비_예산신청서.hwp`(2024년 문서), `4월/샘플_2026예산집행보고서(수정).hwpx`는 장부 대상 아님
- [x] 2단계 (2026-10-23) core 엔진: models, ledger, validate, issues, export + tests 36개
  - 현재 실집행 누계 23,694,010원(문서 기재 23,216,860원, 차이는 I-013·I-016·I-018로 설명), 다음문서번호 53
  - 회귀 기준 누계 단절: 순번 32(−1,329,705), 37(+1,332,555), 38(−480,000)
  - scan_ledger 새 이슈 후보 3건(사용자 판단 대기, 미등록): 순번 3·34 보고서 금액 구조, 순번 53 P11 계획 초과(4,598,000 > 4,320,000)
  - 이력 파일 ledger_history.csv → history.csv
- [ ] 3단계 hwpx 템플릿 채우기(hwpx/)
- [ ] 4단계 Claude 연동: 영수증·메모에서 정보 추출, 경과보고 문장 작성(agents/)
- [ ] 5단계 Streamlit 대시보드(app/)
