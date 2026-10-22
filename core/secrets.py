"""민감값(연락처·계좌)은 .streamlit/secrets.toml에서 읽는다. settings.json·장부에는 넣지 않는다.

secrets.toml 예:
    [연락처]  기안자 = "..."  기안단체장 = "..."
    [계좌]    은행명 = "..."  계좌번호 = "..."  예금주 = "..."        # 기본 계좌
    [계좌_문서별."(예)2026-037"]  예금주 = "..."                     # 문서번호별 덮어쓰기
"""
import tomllib
from pathlib import Path

from core.models import DocBase
from core.paths import ROOT

SECRETS_TOML = ROOT / ".streamlit" / "secrets.toml"
ACCOUNT_FIELDS = ("은행명", "계좌번호", "예금주")


def load_secrets(path=SECRETS_TOML) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def apply_secrets(doc: DocBase, secrets: dict) -> DocBase:
    """연락처·계좌를 채운 사본. 계좌 칸별 우선순위:
    doc.계좌_override > secrets 계좌_문서별[문서번호] > 문서에 이미 적힌 값 > secrets 기본 계좌."""
    contact = secrets.get("연락처", {})
    default = secrets.get("계좌", {})
    per_doc = secrets.get("계좌_문서별", {}).get(doc.문서번호, {})
    override = doc.계좌_override.model_dump() if doc.계좌_override else {}
    update = {}
    for key, value in (("기안자연락처", contact.get("기안자", "")),
                       ("기안단체장연락처", contact.get("기안단체장", ""))):
        if value and not getattr(doc, key):
            update[key] = value
    for key in ACCOUNT_FIELDS:
        value = override.get(key) or per_doc.get(key) or getattr(doc, key) or default.get(key, "")
        if value != getattr(doc, key):
            update[key] = value
    return doc.model_copy(update=update)
