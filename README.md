# church-budget-agent (개발자용)

개포동성당 초등부 교사회 예산신청서·집행보고서(hwpx) 생성, 회계 장부·이슈 관리 Streamlit 앱.
운영은 회계담당 1인이 앱으로 하고(사용법은 [docs/사용설명서.md](docs/사용설명서.md)), 개발자는 배포·API 키 교체만 맡는다.
도메인 규칙과 상세 구조는 [CLAUDE.md](CLAUDE.md).

## 구조

```
app/      Streamlit 앱(진입점 app/Home.py, 화면은 app/views/)
core/     장부·이슈·계산·저장(로컬/GitHub)·암호화 설정·회계연도 전환 — UI·LLM 의존 없음
agents/   Claude 호출(정보 추출·문장 작성만. 금액 계산은 core)
hwpx/     hwpx 채우기·읽기, 영수증 첨부 docx
data/     장부(연도별 data/{연도}/), settings.json, settings_private.enc(암호화)
scripts/  gen_settings_key.py(암호화 키), check_cloud.py(배포 점검), eval_extraction.py
docs/     사용설명서.md(앱 안에서도 보임), 회계담당_접속안내.md
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

- 실제 장부(data/)를 건드리지 않고 시험하려면 사본을 만들어 `BUDGET_DATA_DIR=/tmp/사본/data`로 실행한다.
- `.streamlit/config.toml`: 테마, 업로드 한 파일당 30MB.

## 테스트

```bash
budget_env/bin/python -m pytest tests                                  # 단위·앱 시나리오(API 호출 없음)
APP_ENV=dev budget_env/bin/python -m pytest tests/test_agents_live.py --live   # 실제 Claude API 스모크 3건
budget_env/bin/python scripts/check_cloud.py                           # secrets·GitHub·암호화·Claude·hwpx·xlsx 점검
```

`check_cloud.py`는 비밀값을 출력하지 않고, 운영 브랜치는 읽기만 한다(쓰기 시험은 임시 브랜치를 만들었다 지움).

## 브랜치와 데이터

| 브랜치 | 내용 | 누가 쓰나 |
|---|---|---|
| `main` | 앱 코드(Streamlit Cloud 배포 브랜치) | 개발자 push |
| `data` | 장부 데이터 `data/` (+ 만들 때의 코드 사본) | 앱이 GitHub API로 커밋 |

- Streamlit Cloud는 배포 브랜치에 push가 생길 때마다 앱을 재시작한다. 장부 저장을 `data` 브랜치에 하므로 저장해도 재시작되지 않는다.
- 배포 후에는 **`data` 브랜치가 장부의 원본**이다. `main`의 `data/`는 배포 시점 사본(로컬 개발·테스트용)이며 갱신하지 않는다.
  두 브랜치를 서로 merge하지 않는다.
- 로컬에서 최신 장부가 필요하면: `git fetch origin data && git restore --source origin/data -- data/` (커밋하지 말 것) 또는 앱 › 인수인계 › 패키지.
- 데이터 형식을 바꾸는 코드 변경(열 추가 등)은 `data` 브랜치 파일도 함께 옮겨야 한다. 앱이 쓰지 않는 시간에 한다.
- 앱의 커밋 메시지: `장부: (예)2026-053 … 신청서 추가`, `이슈: I-019 해결(설명됨)`, `되돌리기: H-0012`, `설정: …`

## 비밀값

`.streamlit/secrets.toml.example`에 항목·용도·만드는 법이 있다. 요약:

| 키 | 용도 |
|---|---|
| ANTHROPIC_API_KEY | Claude API |
| GITHUB_TOKEN | fine-grained, 이 저장소 Contents 읽기/쓰기만 |
| GITHUB_REPO | `<계정>/church-budget-agent` |
| DATA_BRANCH | `"data"` |
| SETTINGS_KEY | Fernet 키 — `budget_env/bin/python scripts/gen_settings_key.py` |
| ADMIN_PASSWORD | 최초 접속·비상 복구 |
| VIEWER_PASSWORD | 선택(열람 전용) |
| APP_ENV | `"prod"`(클라우드) / `"dev"`(로컬) |

> ⚠️ **SETTINGS_KEY를 잃어버리면** `data/settings_private.enc`(관리자 비밀번호 해시·연락처·계좌)를 **복구할 수 없다.**
> 키는 개인 PC 밖 두 곳(교사회 공용 계정의 비밀번호 관리자, 인쇄본을 봉투에 넣어 본당 금고 등)에 보관한다.
> 메신저·이메일에 남기지 않고 저장소에 커밋하지 않는다. 운영 중에는 새로 만들지 않는다.
> 잃어버렸다면: 새 키를 만들어 넣고 `settings_private.enc`를 data 브랜치에서 지운 뒤, ADMIN_PASSWORD로 들어가
> 비밀번호·연락처·계좌를 다시 입력한다(장부는 영향 없음).

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
4. `budget_env/bin/python scripts/gen_settings_key.py`로 SETTINGS_KEY를 만들고 보관한다.
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
6. 앱 › Settings › **Sharing**: 비공개 저장소 앱은 기본으로 초대한 사람만 볼 수 있다. 회계담당(필요하면 열람자) 이메일을 초대하거나,
   앱 자체 비밀번호만으로 충분하다고 판단하면 공개로 바꾼다.

Secrets를 나중에 바꾸려면: 앱 › ⋮ › Settings › Secrets → 저장하면 잠시 뒤 반영(바로 반영하려면 ⋮ › Reboot app).

### 참고
- 앱이 한동안(며칠) 쓰이지 않으면 잠든다. 접속하면 "This app has gone to sleep… Yes, get this app back up!" 버튼이 나오고, 누르면 1분 안팎으로 깨어난다. 데이터는 data 브랜치에 있으므로 잃지 않는다.
- Reboot·재배포 때 서버의 임시 파일은 지워지지만 장부는 GitHub에 있으므로 유지된다.

## 운영 계정

GitHub·Streamlit Cloud·Anthropic Console은 **교사회 공용 이메일 계정**으로 운영하는 것을 권장한다(담당자가 바뀌어도 계정이 남음).

개인 계정에서 공용 계정으로 옮기기:
1. 공용 이메일로 GitHub 계정을 만든다.
2. 저장소 › Settings › General › Danger Zone › **Transfer ownership** → 공용 계정으로 이전(공용 계정에서 수락).
   이전 후 주소가 `<공용계정>/church-budget-agent`로 바뀐다.
3. 공용 계정으로 fine-grained 토큰을 새로 발급한다(이전 토큰은 GitHub › Settings에서 삭제).
4. 기존 Streamlit 앱의 Secrets 내용을 안전한 곳에 복사해 둔 뒤, 기존 앱을 삭제한다(⋮ › Settings › Delete — 주소가 풀림).
5. 공용 계정으로 share.streamlit.io에 로그인해 위 '클릭할 단계'대로 다시 배포한다. Secrets에서 `GITHUB_TOKEN`, `GITHUB_REPO`만 새 값으로 바꾼다
   (`SETTINGS_KEY`는 **그대로** — 바꾸면 settings_private.enc를 못 푼다).
6. `scripts/check_cloud.py`로 점검하고 로컬 원격 주소를 바꾼다: `git remote set-url origin https://github.com/<공용계정>/church-budget-agent.git`

## API 키 교체

1. 회계담당이 https://console.anthropic.com 에 (공용) 계정을 만들고 **Settings › Limits에서 월 사용 한도**를 정한 뒤 API 키를 발급한다.
2. 키를 개발자에게 **안전하게** 전달한다(직접 만나 전달하거나 비밀번호 관리자 공유 등). 메신저로 보냈다면 **전달에 쓴 메시지를 양쪽에서 삭제**한다.
3. 개발자가 Streamlit Cloud › 앱 › Settings › Secrets의 `ANTHROPIC_API_KEY`를 바꾸고 **Reboot app** 한다.
4. 회계담당이 앱 › 설정 › 관리자에서 'Claude API 키: 설정됨', 끝 4자리가 새 키와 같은지 확인하고, 영수증 읽기 등 한 번 써서 '마지막 호출 성공'이 갱신되는지 본다.
5. Console에서 **이전 키를 폐기(Disable/Delete)** 한다.

## 비상 복구

- **관리자 비밀번호 분실**: secrets의 `ADMIN_PASSWORD`로 로그인 → 설정 › 관리자에서 새 비밀번호를 정한다(앱 비밀번호가 우선하지만 ADMIN_PASSWORD는 항상 '비상 복구'로 들어갈 수 있다).
- **장부 백업**: GitHub › 저장소 › 브랜치 `data` › Code › Download ZIP, 또는 앱 › 대시보드 엑셀 / 인수인계 패키지.
- **잘못된 반영**: 먼저 앱 › 오류 관리 › 이력에서 그 반영을 **되돌리기**(장부·이슈 함께 복원, 기록 남음).
  그 뒤 같은 칸이 다시 바뀌어 되돌리기가 거부되면, GitHub에서 `data` 브랜치의 해당 커밋을 되돌린다:
  ```bash
  git fetch origin data && git switch data && git revert <커밋> && git push origin data
  ```
  (앱에서 ↻ 새로 불러오기). 앱이 쓰는 중이면 충돌할 수 있으니 회계담당과 시간을 맞춘다.
- **SETTINGS_KEY 분실**: 위 '비밀값' 참고.
