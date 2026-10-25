# church-budget-agent (개발자용)

개포동성당 초등부 교사회 **집행보고서(서식2) hwpx + 영수증 첨부 docx 생성** Streamlit 앱. 화면은 집행 보고서·설정 두 개.
행사·목적·일시·참가인원(입력 그대로 해당 칸에, 박·일은 자동 계산)과 영수증 이미지를 받아 경과보고(Claude, 사업계획 맥락 포함)·세부내역(Claude 묶음, 금액은 코드)·합계(B)(코드)와 설정값만 채우고 나머지 칸은 빈 양식 그대로 둔다. 장부는 없다.
운영은 회계담당 1인이 하고(사용법은 [docs/사용설명서.md](docs/사용설명서.md)), 개발자는 배포·API 키 교체·앱 공유 설정만 맡는다.

> **앱에는 로그인·암호화가 없다.** 들어오면 모든 기능을 쓸 수 있고, 계좌·연락처는 `data/settings.json`에 평문으로 저장된다.
> 접근 제한은 **Streamlit 앱 공유 설정('초대한 사람만')**과 **GitHub 비공개 저장소**로만 한다. 둘 다 반드시 지킨다.
도메인 규칙과 상세 구조는 [CLAUDE.md](CLAUDE.md).

## 구조

```
app/      Streamlit 앱(진입점 app/Home.py, 화면 app/views/report.py·settings.py)
core/     report.py(보고서 문서 만들기·합계, 설정 읽기·쓰기), models.py, storage.py(로컬/GitHub) — UI·LLM 의존 없음
agents/   Claude 호출: receipt_agent(영수증 읽기), report_agent(세부내역 묶기·경과보고 문장). 금액 계산은 core
hwpx/     hwpx 채우기·읽기, 영수증 첨부 docx
data/     settings.json(설정·연락처·계좌 — main에서는 연락처·계좌 빈칸, 실제 값은 data 브랜치),
          {연도}/plan.csv(사업계획: 시기·사업명·세부내역 — 경과보고 맥락용, 2026_사업계획서 엑셀에서 옮김)
scripts/  check_cloud.py(배포 점검), eval_extraction.py
docs/     사용설명서.md, 회계담당_접속안내.md
tests/    pytest(Claude는 FakeClient 대역)
```

## 로컬 실행

Python **3.13** (로컬 venv와 Streamlit Cloud 모두 3.13으로 맞춘다).

```bash
python3.13 -m venv budget_env
budget_env/bin/pip install -r requirements-dev.txt     # 앱 의존성(버전 고정) + pytest
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 값 채우기. GITHUB_TOKEN을 비우면 로컬 data/ 사용
budget_env/bin/streamlit run app/Home.py
```

- 설정(data/)을 건드리지 않고 시험하려면 사본을 만들어 `BUDGET_DATA_DIR=/tmp/사본/data`로 실행한다. 보고서 생성은 아무것도 저장하지 않는다.
- `.streamlit/config.toml`: 테마, 업로드 한 파일당 30MB.

## 테스트

```bash
budget_env/bin/python -m pytest tests                                  # 단위·앱 시나리오(API 호출 없음)
APP_ENV=dev budget_env/bin/python -m pytest tests/test_agents_live.py --live   # 실제 Claude API 스모크 2건
budget_env/bin/python scripts/check_cloud.py                           # secrets·GitHub·설정·Claude·보고서 hwpx·docx 점검
```

`check_cloud.py`는 비밀값·계좌·연락처를 출력하지 않고, 운영 브랜치는 읽기만 한다(쓰기 시험은 임시 브랜치를 만들었다 지움).

## 브랜치와 데이터

| 브랜치 | 내용 | 누가 쓰나 |
|---|---|---|
| `main` | 앱 코드(Streamlit Cloud 배포 브랜치) | 개발자 push |
| `data` | `data/settings.json`(+ 예전 장부 파일·만들 때의 코드 사본, 앱은 settings.json만 씀) | 앱이 설정 저장 때 GitHub API로 커밋 |

- Streamlit Cloud는 배포 브랜치에 push가 생길 때마다 앱을 재시작한다. 설정 저장은 `data` 브랜치에 하므로 저장해도 재시작되지 않는다.
- 배포 후 설정의 원본은 **`data` 브랜치**다. 계좌·연락처 실제 값은 data 브랜치에만 두고 main의 settings.json은 빈칸으로 둔다.
  두 브랜치를 서로 merge하지 않는다.
- 예전(장부·이슈 기능이 있던 때) 장부 파일은 data 브랜치와 main의 git 이력에 그대로 남아 있다.
- 앱의 커밋 메시지: `설정: 기본 정보·연락처 수정` 등.

## 비밀값(secrets)

`.streamlit/secrets.toml.example`에 항목·용도·만드는 법이 있다. 요약:

| 키 | 용도 |
|---|---|
| ANTHROPIC_API_KEY | Claude API |
| GITHUB_TOKEN | fine-grained, 이 저장소 Contents 읽기/쓰기만 |
| GITHUB_REPO | `<계정>/church-budget-agent` |
| DATA_BRANCH | `"data"` |
| APP_ENV | `"prod"`(클라우드) / `"dev"`(로컬) |

## 배포 (Streamlit Community Cloud)

### 처음 한 번 — 저장소 준비(개발자)

1. GitHub에 **비공개(Private)** 저장소 `church-budget-agent`를 만든다(교사회 공용 계정 권장, 아래 '운영 계정').
2. 로컬에서 원격을 연결하고 두 브랜치를 올린다.
   ```bash
   git remote add origin https://github.com/<계정>/church-budget-agent.git
   git push -u origin main
   git push origin data          # data 브랜치(로컬에 이미 만들어 둠)
   ```
3. fine-grained 토큰 발급: GitHub › Settings › Developer settings › Fine-grained tokens › Generate new token
   - Repository access: Only select repositories → `church-budget-agent`
   - Permissions › Repository permissions › **Contents: Read and write** (나머지는 없음), 만료일 지정
4. **Anthropic Console에서 월 사용 한도 설정**(필수): console.anthropic.com › Settings › Limits. 키가 새도 피해가 한도 안에 머문다.
5. 채운 secrets로 `scripts/check_cloud.py --secrets <파일>`을 돌려 모두 '통과'인지 본다.

### share.streamlit.io에서 클릭할 단계

1. https://share.streamlit.io 에 **GitHub 계정으로** 로그인(저장소 소유 계정). 비공개 저장소 접근 권한 요청이 나오면 허용.
2. 오른쪽 위 **Create app** → **Deploy a public app from GitHub**(이름과 달리 비공개 저장소도 선택 가능).
3. 입력
   - Repository: `<계정>/church-budget-agent`
   - Branch: **`main`** (data가 아님)
   - Main file path: **`app/Home.py`**
   - App URL: 원하는 주소(예: `gaepo-chodeung-budget`)
4. **Advanced settings**
   - Python version: **3.13**
   - Secrets: 채운 secrets.toml 내용을 그대로 붙여 넣기(`APP_ENV = "prod"`, `DATA_BRANCH = "data"`)
5. **Deploy** → 몇 분 뒤 앱이 열린다. 왼쪽 사이드바 아래 '저장소: GitHub …@data'가 보이면 정상.
6. **Streamlit 앱 공유 설정을 '초대한 사람만'으로 제한 — 계좌·연락처가 저장되므로 반드시**(필수):
   앱 › ⋮ › Settings › **Sharing** › Who can view this app = **Only specific people can view this app**,
   회계담당 이메일(필요하면 교사회 공용 이메일)을 초대한다. "This app is public" 은 절대 켜지 않는다.
   초대받은 사람은 그 이메일로 Streamlit(또는 Google/GitHub)에 로그인해야 앱이 열린다.

Secrets를 나중에 바꾸려면: 앱 › ⋮ › Settings › Secrets → 저장하면 잠시 뒤 반영(바로 반영하려면 ⋮ › Reboot app).

### 참고
- 앱이 한동안(며칠) 쓰이지 않으면 잠든다. 접속하면 "This app has gone to sleep… Yes, get this app back up!" 버튼이 나오고, 누르면 1분 안팎으로 깨어난다. 설정은 data 브랜치에 있으므로 잃지 않는다.
- Reboot·재배포 때 화면에서 작성 중이던 보고서 내용은 사라진다(설정은 유지).

## 운영 계정

GitHub·Streamlit Cloud·Anthropic Console은 **교사회 공용 이메일 계정**으로 운영하는 것을 권장한다(담당자가 바뀌어도 계정이 남음).

개인 계정에서 공용 계정으로 옮기기:
1. 공용 이메일로 GitHub 계정을 만든다.
2. 저장소 › Settings › General › Danger Zone › **Transfer ownership** → 공용 계정으로 이전(공용 계정에서 수락).
   이전 후 주소가 `<공용계정>/church-budget-agent`로 바뀐다.
3. 공용 계정으로 fine-grained 토큰을 새로 발급한다(이전 토큰은 GitHub › Settings에서 삭제).
4. 기존 Streamlit 앱의 Secrets 내용을 안전한 곳에 복사해 둔 뒤, 기존 앱을 삭제한다(⋮ › Settings › Delete — 주소가 풀림).
5. 공용 계정으로 share.streamlit.io에 로그인해 위 '클릭할 단계'대로 다시 배포한다. Secrets에서 `GITHUB_TOKEN`, `GITHUB_REPO`만 새 값으로 바꾸고,
   **공유 설정(초대한 사람만)을 다시 지정**한다.
6. `scripts/check_cloud.py`로 점검하고 로컬 원격 주소를 바꾼다: `git remote set-url origin https://github.com/<공용계정>/church-budget-agent.git`

## API 키 교체

1. 회계담당이 https://console.anthropic.com 에 (공용) 계정을 만들고 **Settings › Limits에서 월 사용 한도**를 정한 뒤 API 키를 발급한다.
2. 키를 개발자에게 **안전하게** 전달한다(직접 만나 전달하거나 비밀번호 관리자 공유 등). 메신저로 보냈다면 **전달에 쓴 메시지를 양쪽에서 삭제**한다.
3. 개발자가 Streamlit Cloud › 앱 › Settings › Secrets의 `ANTHROPIC_API_KEY`를 바꾸고 **Reboot app** 한다.
4. 회계담당이 앱 › 설정 › Claude 모델·API 키에서 'Claude API 키: 설정됨', 끝 4자리가 새 키와 같은지 확인하고, 영수증 읽기를 한 번 해 본다.
5. Console에서 **이전 키를 폐기(Disable/Delete)** 한다.

## 담당자가 바뀔 때(앱 공유)

앱 › ⋮ › Settings › Sharing에서 **새 담당자 이메일을 초대하고, 이전 담당자 이메일을 제거**한다.
이전 담당자를 빼지 않으면 계속 계좌·연락처를 볼 수 있다.

## 비상 복구

- **설정 백업·복구**: GitHub › 저장소 › 브랜치 `data` › `data/settings.json`의 History에서 이전 버전을 볼 수 있다.
  잘못 저장했다면 `git fetch origin data && git switch data && git revert <커밋> && git push origin data` 후 앱에서 ↻ 새로 불러오기.
- **보고서를 잘못 만들었을 때**: 앱은 아무것도 저장하지 않으므로 고쳐서 다시 만들면 된다.
- **접근하면 안 되는 사람이 들어왔다면**: Sharing에서 그 이메일을 지우고, 계좌·연락처가 노출됐다고 보고 회계담당에게 알린다.
  API 키는 Console에서 폐기·재발급한다.

## 배포 후 확인 체크리스트

1. **접근 차단**: 로그아웃 상태(시크릿 창)와 초대하지 않은 계정으로 접속하면 앱이 열리지 않는다.
2. 초대한 계정으로 접속하면 바로 집행 보고서가 열리고 메뉴는 집행 보고서·설정 두 개다(앱 비밀번호 화면 없음).
3. 설정에서 연락처·계좌를 입력·저장 → data 브랜치에 `data/settings.json` 커밋이 생기고(main에는 없음) 앱이 재시작되지 않는다.
4. 보고서 1건 테스트: 행사·목적·일시·참가인원 적고 영수증 사진 올리기 → 보고서 만들기 → zip의 hwpx를 한글로 열어 경과보고·합계(B)와
   **설정의 주관단체·기안자·연락처·계좌**, 입력한 행사명·목적·일시(박·일)·인원이 **검정 글씨로** 들어갔는지, 나머지 칸이 빈 양식 그대로인지 확인. 영수증 docx도 연다.
5. 앱 Reboot 후 설정이 유지된다.
6. 휴대폰에서 영수증 사진 업로드와 화면 확인.
