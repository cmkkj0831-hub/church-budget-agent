"""경로 상수와 NFC 정규화 헬퍼. 맥 파일명은 NFD일 수 있으므로 비교 전 항상 NFC로 바꾼다."""
import os
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"
OUT_DIR = ROOT / "out"

SETTINGS_JSON = DATA_DIR / "settings.json"           # 현재 회계연도 포함, 민감값 제외
PRIVATE_ENC = DATA_DIR / "settings_private.enc"      # 민감값(Fernet 암호화)
YEAR_FILES = ("ledger.csv", "plan.csv", "plan_info.csv", "issues.csv", "history.csv", "usage.csv")


def year_dir(year: int, data_dir=DATA_DIR) -> Path:
    """data/{연도}/ — 장부·사업계획·이슈·이력·사용량."""
    return Path(data_dir) / str(year)


def nfc(s) -> str:
    return unicodedata.normalize("NFC", str(s))


def find_child(parent: Path, name: str) -> Path:
    """parent 안에서 NFC 기준으로 이름이 같은 항목을 찾는다."""
    target = nfc(name)
    for entry in os.listdir(parent):
        if nfc(entry) == target:
            return Path(parent) / entry
    raise FileNotFoundError(f"{parent}/{target}")


def source_dir(year: int = 2026) -> Path:
    """원본 문서 폴더('2026년' 또는 '2026')."""
    for name in (f"{year}년", str(year)):
        try:
            return find_child(ROOT, name)
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"{year} 원본 폴더 없음")
