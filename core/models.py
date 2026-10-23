"""도메인 모델. 필드명은 양식·CSV의 한글 칸 이름을 그대로 쓴다."""
import re
from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field

DocKind = Literal["예산신청서", "집행보고서"]
EventType = Literal["행사·교육·피정", "성지순례·친교·기타"]
IssueType = Literal["계산 불일치", "누계 누락", "기재 오류", "문서 누락", "번호 중복", "미확인", "예산 경고", "증빙 누락"]
MAX_LINE_ITEMS = 7  # 양식 세부내역 기본 칸 수(신청서·보고서 공통). 넘으면 행 복제 또는 묶기
RowMode = Literal["clone", "group"]
ISSUE_TYPES_ALL = list(get_args(IssueType))
FormKind = Literal["구", "신"]  # 구: 1~4월 초 양식(보고서 '반납금액' 칸, 신청서 보좌신부 결재란)
IssueState = Literal["미해결", "확인중", "해결-정정", "해결-설명됨"]


class LineItem(BaseModel):
    계정과목: str = ""
    내용: str
    단가: int
    수량: int = 1
    금액: int | None = None  # None이면 build 시 단가 × 수량
    비고: str = ""


class AccountInfo(BaseModel):
    """계좌 정보(설정 기본 계좌 또는 문서별 덮어쓰기). 장부에는 저장하지 않는다."""
    은행명: str = ""
    계좌번호: str = ""
    예금주: str = ""


class DocBase(BaseModel):
    """신청서·보고서 공통 칸. 계산 칸은 build_*가 채운다(None = 미계산)."""
    양식: FormKind = "신"
    월: int                      # 사업이 속한 월(폴더 기준)
    사업ID: str
    작성일: date | None = None
    문서번호: str = ""
    연결문서번호: str = ""        # 짝 문서(신청서↔보고서)
    행사명: str
    행사유형: EventType = "행사·교육·피정"
    주관단체: str = ""
    협조단체: str = ""
    기안자: str = ""
    기안단체장: str = ""
    기안자연락처: str = ""        # 설정(연락처)에서 채움, 장부에 저장하지 않음
    기안단체장연락처: str = ""
    목적: str = ""
    시작: datetime | None = None
    종료: datetime | None = None
    박: int | None = None
    일: int | None = None
    장소: str = ""
    은행명: str = ""              # 설정(계좌)에서 채움, 장부에 저장하지 않음
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


class LedgerRow(BaseModel):
    순번: int = 0
    월: str                       # 원본 표기 '7월'
    구분: DocKind
    작성일: str = ""              # 원본 표기 그대로
    예산배정액: int | None = None
    금번신청액: int | None = None
    신청누계액: int | None = None
    잔여액: int | None = None
    자체예산: int | None = None
    본당보조: int | None = None
    합계: int | None = None
    잔액: int | None = None
    문서번호: str = ""
    행사명: str = ""
    파일명: str = ""
    사업ID: str = ""
    연결문서번호: str = ""
    상태: str = ""
    양식: FormKind = "신"

    @property
    def 월번호(self) -> int:
        return int(re.match(r"\d+", self.월).group())

    @property
    def 신청서(self) -> bool:
        return self.구분 == "예산신청서"

    @property
    def 취소(self) -> bool:
        return self.상태 == "취소"

    @property
    def 참조(self) -> str:
        """연결문서번호에 쓰는 값. 문서번호가 미기재면 '#순번'."""
        return self.문서번호 if self.문서번호 and "미기재" not in self.문서번호 else f"#{self.순번}"


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
    운영비단가: int
    교사수: int
    결재란: list[str]
    신청서번호접두: str  # '{연도}'를 회계연도로 바꿔 쓴다. 예: "(예){연도}-"
    보고서번호접두: str
    기본시작시각: str
    기본종료시각: str


class YearInfo(BaseModel):
    """회계연도별 값(data/settings.json `연도별`)."""
    예산배정액: int
    다음문서번호: int = 1  # 최소값. 실제 다음 번호는 장부의 마지막 번호 + 1과 비교해 큰 쪽
    마감: bool = False     # 지난 연도: 읽기 전용


class UsagePricing(BaseModel):
    """LLM 사용량 비용 계산용(설정에서 입력, 코드에 고정하지 않음)."""
    입력_USD_1M: float = 0
    출력_USD_1M: float = 0
    환율_KRW_USD: float = 0


class Settings(BaseModel):
    기본정보: BasicInfo  # 기본정보.회계연도 = 현재 회계연도
    기안정보: DrafterInfo
    연락처: Contacts = Field(default_factory=Contacts)    # 문서의 기안자·기안단체장 연락처(평문)
    계좌: AccountInfo = Field(default_factory=AccountInfo)  # 문서의 기본 계좌(평문)
    금액문서기준: DocRules
    교사명단: list[str]
    모델: str
    hwpx_row_mode: RowMode = "clone"  # 세부내역이 양식 칸보다 많을 때: clone(행 복제) | group(계정과목별 묶기)
    llm_fallbacks: Literal["off", "default"] = "off"  # 거절 시 서버 측 대체 모델(agents/llm.py 참고). 기본 끔
    연도별: dict[str, YearInfo] = Field(default_factory=dict)
    사용량단가: UsagePricing = Field(default_factory=UsagePricing)
    진행상태: dict = Field(default_factory=dict)  # 회계연도 전환 마법사·인수인계 체크리스트 진행 상태

    @property
    def 현재연도(self) -> int:
        return self.기본정보.회계연도

    def year_info(self, year: int) -> YearInfo:
        return self.연도별[str(year)]

    def fill_contacts(self, doc):
        """문서에 연락처·계좌를 채운 사본. 연락처 = 설정값, 계좌 칸별 = doc.계좌_override(빈 칸 제외) > 설정값.
        설정이 비어 있으면 문서도 빈칸."""
        override = doc.계좌_override.model_dump() if doc.계좌_override else {}
        update = {"기안자연락처": self.연락처.기안자, "기안단체장연락처": self.연락처.기안단체장}
        for key in ("은행명", "계좌번호", "예금주"):
            update[key] = override.get(key) or getattr(self.계좌, key)
        return doc.model_copy(update=update)


class PlanItem(BaseModel):
    사업ID: str
    시기: str
    계속신규: str
    사업명: str
    세부내역: str = ""
    자체예산: int = 0
    본당예산: int = 0
    전년도집행액: int | None = None
    월별: list[int] = Field(default_factory=lambda: [0] * 12)  # 1월~12월


class Issue(BaseModel):
    이슈ID: str
    대상순번: list[int] = Field(default_factory=list)
    대상문서번호: list[str] = Field(default_factory=list)
    유형: IssueType
    설명: str
    근거: str = ""
    관련이슈: list[str] = Field(default_factory=list)
    검증코드: list[str] = Field(default_factory=list)  # 이 이슈가 설명하는 검증 코드(대상 순번 한정)
    상태: IssueState = "미해결"
    해결방식: str = ""
    해결내용: str = ""
    첨부: str = ""
    처리자: str = ""
    처리일: date | None = None
    검토메모: str = ""  # 회계담당이 확인 중에 남긴 메모(상태 '확인중')

    @property
    def 검토대기(self) -> bool:
        return self.열림 and bool(self.검토메모.strip())

    @property
    def 열림(self) -> bool:
        return self.상태 in ("미해결", "확인중")


class Receipt(BaseModel):
    """영수증 첨부 1건. 이미지는 jpg/png/heic 또는 pdf(첫 쪽)."""
    순번: int
    이미지: str
    날짜: date | None = None
    상호: str = ""
    금액: int | None = None
    용도: str = ""


class LedgerChange(BaseModel):
    순번: int
    열: str
    값: int | str | None


class Resolution(BaseModel):
    """정정: 장부 값 변경·행 추가. 설명: 값 유지, 사유 기록(필요 시 상태 열만 변경)."""
    방식: Literal["정정", "설명"]
    내용: str
    처리자: str
    처리일: date = Field(default_factory=date.today)
    변경: list[LedgerChange] = Field(default_factory=list)
    추가행: list[LedgerRow] = Field(default_factory=list)  # 순번·문서번호 비우면 자동
    함께해결: list[str] = Field(default_factory=list)      # 같은 사유로 닫을 이슈ID
    첨부: str = ""


class HistoryEntry(BaseModel):
    """장부·이슈 변경 1건. 같은 반영(되돌리기 단위)의 항목은 이력ID가 같다."""
    이력ID: str
    일시: str
    대상: str = "장부"  # 장부 | 이슈
    이슈ID: str
    순번: int | None = None
    열: str
    변경전: str = ""
    변경후: str = ""
    사유: str = ""
    처리자: str = ""
