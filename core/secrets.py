"""민감값(연락처·계좌)은 .streamlit/secrets.toml에서 읽는다. settings.json·장부에는 넣지 않는다."""
import tomllib
from pathlib import Path

from core.models import DocBase
from core.paths import ROOT

SECRETS_TOML = ROOT / ".streamlit" / "secrets.toml"


def load_secrets(path=SECRETS_TOML) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def apply_secrets(doc: DocBase, secrets: dict) -> DocBase:
    """문서의 빈 연락처·계좌 칸만 secrets 기본값으로 채운 사본. 문서에 적힌 값은 그대로 둔다."""
    contact = secrets.get("연락처", {})
    account = secrets.get("계좌", {})
    defaults = {
        "기안자연락처": contact.get("기안자", ""),
        "기안단체장연락처": contact.get("기안단체장", ""),
        "은행명": account.get("은행명", ""),
        "계좌번호": account.get("계좌번호", ""),
        "예금주": account.get("예금주", ""),
    }
    return doc.model_copy(update={k: v for k, v in defaults.items() if v and not getattr(doc, k)})
