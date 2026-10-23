""".streamlit/secrets.toml 읽기(앱 밖 — 에이전트·스크립트용). 항목은 secrets.toml.example 참고.
연락처·계좌는 secrets가 아니라 data/settings.json(`연락처`, `계좌`)에 있다."""
import tomllib
from pathlib import Path

from core.paths import ROOT

SECRETS_TOML = ROOT / ".streamlit" / "secrets.toml"


def load_secrets(path=SECRETS_TOML) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)
