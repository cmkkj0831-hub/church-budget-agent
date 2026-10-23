"""SETTINGS_KEY(Fernet 키) 만들기: budget_env/bin/python scripts/gen_settings_key.py

data/settings_private.enc(관리자 비밀번호 해시·연락처·계좌)를 이 키로 암호화한다.
키를 잃어버리면 그 파일은 누구도 풀 수 없다. 키를 바꿔도 기존 파일을 풀 수 없다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.private import new_key  # noqa: E402

WARNING = """
⚠️  이 키를 잃어버리면 암호화된 설정(계좌·연락처·관리자 비밀번호 해시)을 복구할 수 없습니다.
    - Streamlit Cloud › 앱 › Settings › Secrets 의 SETTINGS_KEY 에 붙여 넣습니다.
    - 같은 값을 교사회 공용 계정의 비밀번호 관리자(1Password·Bitwarden 등)나
      인쇄해 봉투에 넣어 본당 사무실 금고처럼 개인 PC 밖 두 곳에 보관하세요.
    - 메신저·이메일 본문에 남기지 말고, 저장소(GitHub)에는 절대 커밋하지 마세요.
    - 이미 운영 중이면 새 키를 만들지 마세요(기존 settings_private.enc를 못 풉니다).
"""

if __name__ == "__main__":
    print(f'SETTINGS_KEY = "{new_key()}"')
    print(WARNING)
