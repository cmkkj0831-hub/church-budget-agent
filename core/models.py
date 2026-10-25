"""도메인 모델. 필드명은 양식의 한글 칸 이름을 그대로 쓴다."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["행사·교육·피정", "성지순례·친교·기타"]
MAX_LINE_ITEMS = 7  # 양식 세부내역 기본 칸 수(신청서·보고서 공통). 넘으면 행 복제 또는 묶기
RowMode = Literal["clone", "group"]
FormKind = Literal["구", "신"]  # 구: 1~4월 초 양식(보고서 '반납금액' 칸, 신청서 보좌신부 결재란)


class LineItem(BaseModel):
    계정과목: str = ""
    내용: str
    단가: int
    수량: int = 1
    금액: int | None = None  # None이면 build 시 단가 × 수량
    비고: str = ""


class AccountInfo(BaseModel):
    """계좌 정보(설정 기본 계좌 또는 문서별 덮어쓰기)."""
    은행명: str = ""
    계좌번호: str = ""
    예금주: str = ""


class DocBase(BaseModel):
    """신청서·보고서 공통 칸. 보고서 계산 칸은 core.report.build_report가 채운다(None = 미계산)."""
    양식: FormKind = "신"
    월: int                      # 행사가 속한 월(저장 폴더 기준)
    사업ID: str = ""
    작성일: date | None = None
    문서번호: str = ""
    연결문서번호: str = ""        # 짝 문서(신청서↔보고서)
    행사명: str
    행사유형: EventType = "행사·교육·피정"
    주관단체: str = ""
    협조단체: str = ""
    기안자: str = ""
    기안단체장: str = ""
    기안자연락처: str = ""        # 설정(연락처)에서 채움
    기안단체장연락처: str = ""
    목적: str = ""
    시작: datetime | None = None
    종료: datetime | None = None
    박: int | None = None
    일: int | None = None
    장소: str = ""
    은행명: str = ""              # 설정(계좌)에서 채움
    계좌번호: str = ""
    예금주: str = ""
    계좌_override: AccountInfo | None = None  # 문서별 계좌(예: 예금주가 다른 부서 운영비). 빈 칸은 무시
    참가대상: str = ""
    인원_총: int | None = None
    인원_남: int | None = None
    인원_여: int | None = None
    건의사항: str = ""
    세부내역: list[LineItem] = Field(default_factory=list)
    자체예산: int | None = None
    본당보조: int | None = None

    def 세부합(self) -> int:
        return sum(i.금액 if i.금액 is not None else i.단가 * i.수량 for i in self.세부내역)


class RequestDoc(DocBase):
    """서식1 예산신청서."""
    행사일정: str = ""
    수입: int | None = None       # = 자체예산 + 본당보조
    합계: int | None = None       # = 세부내역 합
    예산배정액: int | None = None
    금번신청액: int | None = None  # = 본당보조
    신청누계액: int | None = None
    잔여액: int | None = None


class ReportDoc(DocBase):
    """서식2 집행보고서. 본당 = min(신청, 실사용), 자체 = max(0, 실사용 − 신청),
    합계B = 실사용 = 본당 + 자체, 예산A = 신청 + 자체, 잔액 = A − B = 신청 − 본당(반납)."""
    경과보고: str = ""
    예산A: int | None = None
    합계B: int | None = None      # = 세부내역 합(실사용액)
    잔액: int | None = None


class BasicInfo(BaseModel):
    본당명: str
    회계연도: int
    분과: str
    주관단체: str
    분과장: str
    기본장소: str
    참가대상: str


class DrafterInfo(BaseModel):
    기안자: str
    기안단체장: str


class Contacts(BaseModel):
    기안자: str = ""
    기안단체장: str = ""


class DocRules(BaseModel):
    보고서번호접두: str = "(집){연도}-"  # '{연도}'를 회계연도로 바꿔 쓴다
    기본시작시각: str = "10:00"
    기본종료시각: str = "18:00"


class Settings(BaseModel):
    """data/settings.json. 예전 파일의 없어진 칸(장부·사용량 등)은 읽을 때 무시하고 저장할 때 빠진다."""
    기본정보: BasicInfo  # 기본정보.회계연도 = 현재 회계연도
    기안정보: DrafterInfo
    연락처: Contacts = Field(default_factory=Contacts)    # 문서의 기안자·기안단체장 연락처(평문)
    계좌: AccountInfo = Field(default_factory=AccountInfo)  # 문서의 기본 계좌(평문)
    금액문서기준: DocRules = Field(default_factory=DocRules)
    교사명단: list[str]
    모델: str
    hwpx_row_mode: RowMode = "clone"  # 세부내역이 양식 칸보다 많을 때: clone(행 복제) | group(계정과목별 묶기)
    llm_fallbacks: Literal["off", "default"] = "off"  # 거절 시 서버 측 대체 모델(agents/llm.py 참고). 기본 끔

    @property
    def 현재연도(self) -> int:
        return self.기본정보.회계연도

    def fill_contacts(self, doc):
        """문서에 연락처·계좌를 채운 사본. 연락처 = 설정값, 계좌 칸별 = doc.계좌_override(빈 칸 제외) > 설정값.
        설정이 비어 있으면 문서도 빈칸."""
        override = doc.계좌_override.model_dump() if doc.계좌_override else {}
        update = {"기안자연락처": self.연락처.기안자, "기안단체장연락처": self.연락처.기안단체장}
        for key in ("은행명", "계좌번호", "예금주"):
            update[key] = override.get(key) or getattr(self.계좌, key)
        return doc.model_copy(update=update)


class Receipt(BaseModel):
    """영수증 첨부 1건. 이미지는 jpg/png/heic 또는 pdf(첫 쪽)."""
    순번: int
    이미지: str
    날짜: date | None = None
    상호: str = ""
    금액: int | None = None
    용도: str = ""
