"""집행보고서 만들기(장부·입력칸 없음)와 설정 파일 읽기·쓰기. UI·LLM 의존성 없음.

hwpx에 채우는 칸: 경과보고(Claude가 영수증으로 쓴 문장), 합계(B) = 영수증 금액 합(코드가 계산),
설정값(주관단체·기안자·기안단체장·기본 장소·참가대상, 연락처·계좌는 렌더링 때 Settings.fill_contacts).
그 밖의 칸(문서번호·행사명·작성일·일시·인원·세부내역·본당보조·자체예산·예산(A)·잔액)은 빈칸으로 둔다.
"""
import json
from datetime import date

from core.models import ReportDoc, Settings
from core.paths import DATA_DIR

SETTINGS_FILE = "settings.json"


def load_settings(data_dir=DATA_DIR) -> Settings:
    with open(data_dir / SETTINGS_FILE, encoding="utf-8") as f:
        return Settings(**json.load(f))


def save_settings(settings: Settings, data_dir=DATA_DIR) -> None:
    with open(data_dir / SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write(settings.model_dump_json(indent=2) + "\n")


def receipts_total(receipts) -> int:
    """영수증 금액 합(읽지 못한 금액은 0으로 — 화면에서 경고)."""
    return sum(r.금액 or 0 for r in receipts)


def build_report(settings: Settings, 경과보고: str, 합계: int) -> ReportDoc:
    """경과보고·합계(B)와 설정값만 채운 보고서. 나머지 칸은 빈칸."""
    base, drafter = settings.기본정보, settings.기안정보
    return ReportDoc(월=0, 행사명="", 경과보고=경과보고, 합계B=합계,
                     주관단체=base.주관단체, 장소=base.기본장소, 참가대상=base.참가대상,
                     기안자=drafter.기안자, 기안단체장=drafter.기안단체장)


def file_stem(receipts, today: date) -> str:
    """파일 이름 앞부분: 집행보고서_{가장 이른 영수증 날짜(없으면 오늘) YYYYMMDD}."""
    days = sorted(r.날짜 for r in receipts if r.날짜)
    return f"집행보고서_{(days[0] if days else today):%Y%m%d}"
