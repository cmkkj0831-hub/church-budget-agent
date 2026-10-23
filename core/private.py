"""민감 설정(data/settings_private.enc, Fernet 암호화). 키는 Streamlit secrets의 SETTINGS_KEY.

- 관리자 비밀번호 bcrypt 해시: 앱에서 바꾼다. 설정되면 secrets의 ADMIN_PASSWORD보다 우선한다.
  secrets의 ADMIN_PASSWORD는 최초 접속과 비상 복구용(복구 로그인이면 새 비밀번호를 정하게 한다).
- 연락처(기안자, 기안단체장), 계좌(은행명, 계좌번호, 예금주), 문서별 계좌.
API 키는 여기 두지 않는다(secrets에서만 관리).
"""
import hmac
import json
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

from core.paths import PRIVATE_ENC


class PrivateError(RuntimeError):
    pass


class Contacts(BaseModel):
    기안자: str = ""
    기안단체장: str = ""


class Account(BaseModel):
    은행명: str = ""
    계좌번호: str = ""
    예금주: str = ""


class PrivateSettings(BaseModel):
    관리자비밀번호해시: str = ""
    연락처: Contacts = Field(default_factory=Contacts)
    계좌: Account = Field(default_factory=Account)
    계좌_문서별: dict[str, Account] = Field(default_factory=dict)

    def secrets_view(self) -> dict:
        """core.secrets.apply_secrets가 받는 모양({연락처, 계좌, 계좌_문서별})."""
        d = self.model_dump()
        d.pop("관리자비밀번호해시")
        return d


def new_key() -> str:
    """SETTINGS_KEY로 쓸 새 키(처음 배포할 때 한 번)."""
    return Fernet.generate_key().decode()


def _fernet(key: str) -> Fernet:
    if not key:
        raise PrivateError("secrets에 SETTINGS_KEY가 없습니다")
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise PrivateError("SETTINGS_KEY 형식이 맞지 않습니다(Fernet 키)") from e


def load_private(key: str, path=PRIVATE_ENC) -> PrivateSettings:
    path = Path(path)
    if not path.exists():
        return PrivateSettings()
    try:
        raw = _fernet(key).decrypt(path.read_bytes())
    except InvalidToken as e:
        raise PrivateError("민감 설정을 풀 수 없습니다(SETTINGS_KEY가 바뀌었거나 파일이 손상됨)") from e
    return PrivateSettings(**json.loads(raw.decode("utf-8")))


def save_private(data: PrivateSettings, key: str, path=PRIVATE_ENC) -> None:
    token = _fernet(key).encrypt(json.dumps(data.model_dump(), ensure_ascii=False).encode("utf-8"))
    Path(path).write_bytes(token)


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise PrivateError("비밀번호는 8자 이상이어야 합니다")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def check_admin(password: str, private: PrivateSettings, secret_password: str = "") -> str | None:
    """'hash'(앱에서 정한 비밀번호) / 'secret'(secrets 비밀번호: 최초 접속·비상 복구) / None."""
    if not password:
        return None
    if private.관리자비밀번호해시 and bcrypt.checkpw(password.encode("utf-8"),
                                                     private.관리자비밀번호해시.encode()):
        return "hash"
    if secret_password and hmac.compare_digest(password, secret_password):
        return "secret"
    return None


def change_admin_password(private: PrivateSettings, current: str, new: str, secret_password: str = "") -> None:
    if not check_admin(current, private, secret_password):
        raise PrivateError("현재 비밀번호가 맞지 않습니다")
    if new == secret_password:
        raise PrivateError("secrets의 비상용 비밀번호와 다른 비밀번호를 정하세요")
    private.관리자비밀번호해시 = hash_password(new)


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    return "•" * max(0, len(value) - keep) + value[-keep:]
