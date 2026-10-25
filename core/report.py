"""집행보고서 계산(장부 없음)과 설정 파일 읽기·쓰기. UI·LLM 의존성 없음.

보고서 금액 규칙(신청액 = 회계담당이 입력한 연결 신청서 금번신청액):
  본당보조 = min(신청액, 실사용액), 자체예산 = max(0, 실사용액 − 신청액), 합계(B) = 실사용액,
  예산(A) = 신청액 + 자체예산, 잔액(A − B) = 신청액 − 본당보조(반납액).
금액은 모두 코드가 계산한다. LLM은 세부내역 묶음·경과보고 문장만 만든다.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date

from core.models import MAX_LINE_ITEMS, ReportDoc, Settings
from core.paths import DATA_DIR

SETTINGS_FILE = "settings.json"


def load_settings(data_dir=DATA_DIR) -> Settings:
    with open(data_dir / SETTINGS_FILE, encoding="utf-8") as f:
        return Settings(**json.load(f))


def save_settings(settings: Settings, data_dir=DATA_DIR) -> None:
    with open(data_dir / SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write(settings.model_dump_json(indent=2) + "\n")


def report_amounts(applied: int, used: int) -> tuple[int, int, int, int, int]:
    """보고서 금액 규칙 → (본당보조, 자체예산, 합계B, 예산A, 잔액)."""
    subsidy = min(applied, used)
    own = max(0, used - applied)
    return subsidy, own, used, applied + own, applied - subsidy


def doc_number_prefix(settings: Settings) -> str:
    return settings.금액문서기준.보고서번호접두.replace("{연도}", str(settings.현재연도))


def file_name(doc: ReportDoc) -> str:
    """권장 파일명: {행사명}_보고서.hwpx (파일에 쓸 수 없는 문자는 _)."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", doc.행사명).strip() or "집행보고서"
    return f"{safe}_보고서.hwpx"


@dataclass
class ReportBuild:
    doc: ReportDoc
    errors: list[str] = field(default_factory=list)    # 있으면 만들지 않는다
    warnings: list[str] = field(default_factory=list)  # 확인 후 진행


def build_report(draft: ReportDoc, settings: Settings, 신청액: int) -> ReportBuild:
    """초안 + 신청액 → 금액을 채운 보고서. 기본값: 주관단체·기안자·장소·참가대상·작성일은 설정·오늘."""
    doc = draft.model_copy(deep=True)
    base = settings.기본정보
    doc.작성일 = doc.작성일 or date.today()
    doc.주관단체 = doc.주관단체 or base.주관단체
    doc.기안자 = doc.기안자 or settings.기안정보.기안자
    doc.기안단체장 = doc.기안단체장 or settings.기안정보.기안단체장
    doc.장소 = doc.장소 or base.기본장소
    doc.참가대상 = doc.참가대상 or base.참가대상
    doc.세부내역 = [i if i.금액 is not None else i.model_copy(update={"금액": i.단가 * i.수량}) for i in doc.세부내역]
    used = doc.세부합()
    doc.본당보조, doc.자체예산, doc.합계B, doc.예산A, doc.잔액 = report_amounts(max(신청액, 0), used)

    out = ReportBuild(doc)
    if not doc.행사명.strip():
        out.errors.append("행사명을 입력하세요.")
    if not doc.세부내역:
        out.errors.append("세부내역이 없습니다(영수증을 읽고 초안을 만드세요).")
    if 신청액 < 0:
        out.errors.append("신청액은 0 이상이어야 합니다.")
    accounts = {i.계정과목 for i in doc.세부내역}
    if settings.hwpx_row_mode == "group" and len(doc.세부내역) > MAX_LINE_ITEMS and len(accounts) > MAX_LINE_ITEMS:
        out.errors.append(f"계정과목 {len(accounts)}종 > 양식 칸 {MAX_LINE_ITEMS}행: 묶기(group)로 맞출 수 없습니다.")
    if not doc.문서번호.strip():
        out.warnings.append("문서번호가 비어 있습니다(문서에도 빈칸으로 들어갑니다).")
    if not doc.연결문서번호.strip():
        out.warnings.append("연결 신청서 번호가 비어 있습니다.")
    if 신청액 == 0:
        out.warnings.append("신청액이 0원입니다 — 실사용액 전부가 자체예산으로 잡힙니다.")
    if doc.시작 is None:
        out.warnings.append("행사 일시가 비어 있습니다.")
    if doc.작성일 and doc.작성일.year != settings.현재연도:
        out.warnings.append(f"작성일 {doc.작성일}이 회계연도 {settings.현재연도}년과 다릅니다.")
    return out
