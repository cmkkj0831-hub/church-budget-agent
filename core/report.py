"""집행보고서 만들기(장부·입력칸 없음)와 설정 파일 읽기·쓰기. UI·LLM 의존성 없음.

hwpx에 채우는 칸: 행사명·목적·일시(박·일은 날짜로 자동 계산)·인원(회계담당이 적은 그대로),
경과보고(Claude가 영수증·행사·사업계획 맥락으로 쓴 문장), 세부내역(계정과목·정산세부내용은 Claude의 영수증 묶음,
금액 = 묶은 영수증 합, 단가 = 금액·수량 1, 비고 = 영수증 번호), 합계(B) = 영수증 금액 합(코드),
설정값(주관단체·기안자·기안단체장·기본 장소·참가대상, 연락처·계좌는 렌더링 때 Settings.fill_contacts).
그 밖의 칸(문서번호·작성일·본당보조·자체예산·예산(A)·잔액)은 빈칸으로 둔다.
"""
import csv
import json
from datetime import date, datetime

from core.models import LineItem, ReportDoc, Settings
from core.paths import DATA_DIR

SETTINGS_FILE = "settings.json"


def load_settings(data_dir=DATA_DIR) -> Settings:
    with open(data_dir / SETTINGS_FILE, encoding="utf-8") as f:
        return Settings(**json.load(f))


def save_settings(settings: Settings, data_dir=DATA_DIR) -> None:
    with open(data_dir / SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write(settings.model_dump_json(indent=2) + "\n")


def load_plan(data_dir=DATA_DIR, year: int | None = None) -> list[dict]:
    """사업계획(data/{연도}/plan.csv, 2026_사업계획서 엑셀에서 옮긴 것)의 시기·사업명·세부내역. 없으면 빈 목록."""
    if year is None:
        year = load_settings(data_dir).현재연도
    path = data_dir / str(year) / "plan.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: (r.get(k) or "").strip() for k in ("시기", "사업명", "세부내역")} for r in csv.DictReader(f)]


def nights_days(start: date | None, end: date | None) -> tuple[int | None, int | None]:
    """일시로 박·일 자동 계산: 박 = 종료일 − 시작일, 일 = 박 + 1(당일 행사는 0박 1일). 시작일이 없으면 (None, None)."""
    if start is None:
        return None, None
    nights = max(0, ((end or start) - start).days)
    return nights, nights + 1


def receipts_total(receipts) -> int:
    """영수증 금액 합(읽지 못한 금액은 0으로 — 화면에서 경고)."""
    return sum(r.금액 or 0 for r in receipts)


def build_report(settings: Settings, 경과보고: str, 합계: int, 행사명: str = "", 목적: str = "",
                 시작: datetime | None = None, 종료: datetime | None = None, 인원_총: int | None = None,
                 인원_남: int | None = None, 인원_여: int | None = None,
                 세부내역: list[LineItem] | None = None) -> ReportDoc:
    """입력한 행사명·목적·일시·인원, 경과보고·세부내역·합계(B)와 설정값만 채운 보고서. 박·일은 일시로 계산. 나머지 칸은 빈칸."""
    base, drafter = settings.기본정보, settings.기안정보
    nights, days = nights_days(시작.date() if 시작 else None, 종료.date() if 종료 else None)
    return ReportDoc(월=0, 행사명=행사명.strip(), 목적=목적.strip(), 경과보고=경과보고, 합계B=합계,
                     시작=시작, 종료=종료, 박=nights, 일=days, 인원_총=인원_총, 인원_남=인원_남, 인원_여=인원_여,
                     세부내역=[i.model_copy(update={"금액": i.단가 * i.수량}) if i.금액 is None else i for i in 세부내역 or []],
                     주관단체=base.주관단체, 장소=base.기본장소, 참가대상=base.참가대상,
                     기안자=drafter.기안자, 기안단체장=drafter.기안단체장)


def file_stem(receipts, today: date) -> str:
    """파일 이름 앞부분: 집행보고서_{가장 이른 영수증 날짜(없으면 오늘) YYYYMMDD}."""
    days = sorted(r.날짜 for r in receipts if r.날짜)
    return f"집행보고서_{(days[0] if days else today):%Y%m%d}"
