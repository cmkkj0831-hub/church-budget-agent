"""hwpx 생성: 실제 작성 예시(5월 어린이날)를 데이터로 다시 만들어 칸별로 비교한다."""
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import pytest

from core.models import AccountInfo, LineItem, ReportDoc, RequestDoc
from core.paths import TEMPLATES_DIR
from core.report import build_report
from hwpx.fill import REPORT_CELLS, REQUEST_CELLS, REPORT_TEMPLATE, REQUEST_TEMPLATE, render
from hwpx.layout import SENSITIVE
from hwpx.reader import read_document
from hwpx.text import read_cells
from tests.conftest import TODAY

EXAMPLES = TEMPLATES_DIR / "examples"

COMMON = dict(작성일=date(2026, 5, 9), 행사명="초등부 어린이 날 행사", 주관단체="초등부 교사회",
              기안자="정연재 레오", 기안단체장="정지연 플로라", 목적="어린이 날 행사 진행",
              참가대상="초등부 주일학교", 인원_총=130, 인원_남=65, 인원_여=65, 월=5, 사업ID="P05")

CHILDRENS_REQUEST = RequestDoc(
    **COMMON, 문서번호="(예)2026-024", 장소="개포동성당",
    시작=datetime(2026, 5, 1, 10), 종료=datetime(2026, 5, 9, 19),
    행사일정="5월 9일 어린이 미사 이후 지하 1층에서 어린이 날 행사 예정입니다.\n"
            "행사 준비와 진행에 필요한 비용 신청합니다.\n\n"
            "-행사 부스 물품 구매\n-행사 상품 구매\n-행사 간식 준비\n-기타 비용",
    세부내역=[LineItem(내용="행사 상품 구매", 단가=160_000), LineItem(내용="행사 부스 물품 구매", 단가=56_000),
            LineItem(내용="행사 간식 준비", 단가=110_000), LineItem(내용="기타 비용", 단가=174_000)],
    수입=490_000, 자체예산=0, 본당보조=490_000, 합계=490_000,
    예산배정액=36_470_000, 금번신청액=490_000, 신청누계액=11_909_660, 잔여액=24_560_340)

CHILDRENS_REPORT = ReportDoc(
    **COMMON, 문서번호="(집)2026-025", 장소="개포동 성당",
    시작=datetime(2026, 5, 1, 10), 종료=datetime(2026, 5, 9, 23),
    경과보고="5월 9일 어린이 미사 이후 지하 층에서 어린이 날 행사 진행하였습니다.\n"
            "지하 1층 강당과 교리실을 활용해 행사 진행하였습니다.\n\n"
            "행사 준비와 진행을 위해 예산 사용처\n"
            "-추첨권 추첨 상품 구매(스마트 워치, 기프트 카드, 키캡)\n-행사 간식 비용\n-행사 부스 물품 비용\n"
            "-행사 준비 기간 중 회의 비용 및 회의 후 식대",
    세부내역=[LineItem(계정과목="초등부 교사회", 내용="추첨권 상품", 단가=132_030),
            LineItem(계정과목="초등부 교사회", 내용="행사 간식", 단가=115_300),
            LineItem(계정과목="초등부 교사회", 내용="행사 부스 물품", 단가=53_240),
            LineItem(계정과목="초등부 교사회", 내용="행사 회의 후 식대", 단가=157_500),
            LineItem(계정과목="초등부 교사회", 내용="행사 회의 비용", 단가=32_000)],
    예산A=490_060, 자체예산=60, 본당보조=490_060, 합계B=490_060, 잔액=0)


def norm(lines) -> list[str]:
    """공백 차이와 금액 뒤 '원' 유무는 무시하고, 빈 줄은 뺀다."""
    out = [re.sub(r"\s+", "", t) for t in lines or []]
    return [re.sub(r"(?<=\d)원$", "", t) for t in out if t]


@pytest.mark.parametrize("doc, template, example, cells", [
    (CHILDRENS_REQUEST, REQUEST_TEMPLATE, "request_childrensday.hwpx", REQUEST_CELLS),
    (CHILDRENS_REPORT, REPORT_TEMPLATE, "report_childrensday.hwpx", REPORT_CELLS),
])
def test_matches_real_example(tmp_path, doc, template, example, cells):
    if not (EXAMPLES / example).exists():
        pytest.skip("작성 예시 없음(커밋 제외 파일)")
    out = read_cells(render(doc, tmp_path / "out.hwpx"))
    want = read_cells(EXAMPLES / example)
    blank = read_cells(template)
    skip = {cells[k] for k in SENSITIVE}
    filled = [k for k in want if want[k] != blank.get(k) and k not in skip]
    assert len(filled) > 20
    for k in filled:
        assert norm(out[k]) == norm(want[k]), k
    for k in blank:  # 채우지 않은 칸은 양식 그대로
        if k not in filled and k not in skip:
            assert out[k] == blank[k], k


def test_package_is_valid(tmp_path):
    path = render(CHILDRENS_REQUEST, tmp_path / "a.hwpx")
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(REQUEST_TEMPLATE) as t:
        infos = z.infolist()
        assert infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED
        assert [i.filename for i in infos] == [i.filename for i in t.infolist()]
        for i in infos:
            if i.filename.endswith((".xml", ".hpf", ".rdf")):
                ET.fromstring(z.read(i))
        section = z.read("Contents/section0.xml").decode()
        assert section.count("<hp:tc ") == t.read("Contents/section0.xml").decode().count("<hp:tc ")
        assert "초등부 어린이 날 행사" in z.read("Preview/PrvText.txt").decode()


def test_special_characters_escaped(tmp_path):
    doc = CHILDRENS_REQUEST.model_copy(update={"행사명": "A & B <행사>"})
    cells = read_cells(render(doc, tmp_path / "a.hwpx"))
    assert cells[REQUEST_CELLS["행사명"]] == ["A & B <행사>"]


def test_report_only_body_total_and_settings(settings, tmp_path):
    """행사명·목적(입력 그대로)·경과보고·합계(B)·설정값만 채우고 나머지는 빈 양식 그대로."""
    settings.연락처.기안자, settings.계좌.예금주 = "010-0000-0001", "홍길동"
    doc = settings.fill_contacts(build_report(settings, "10월 교리 간식을 다음과 같이 사용하였습니다.", 55_000,
                                              "10월 교리 & <간식>", "어린이 격려\n교사회 운영"))
    path = render(doc, tmp_path / "r.hwpx")
    rep = read_cells(path)
    assert rep[REPORT_CELLS["합계B"]] == ["55,000원"]
    assert rep[REPORT_CELLS["행사명"]] == ["10월 교리 & <간식>"] and rep[REPORT_CELLS["목적"]] == ["어린이 격려", "교사회 운영"]  # 줄마다 문단
    assert rep[REPORT_CELLS["기안자"]][0].startswith("정연재 레오") and rep[REPORT_CELLS["주관단체"]] == ["초등부 교사회"]
    assert rep[REPORT_CELLS["기안자연락처"]] == ["010-0000-0001"] and rep[REPORT_CELLS["예금주"]] == ["홍길동"]
    blank = read_cells(REPORT_TEMPLATE)
    for key in ("문서번호", "작성일", "일시", "인원", "본당보조", "자체예산", "예산A", "잔액"):
        assert rep[REPORT_CELLS[key]] == blank[REPORT_CELLS[key]], key  # 빈 양식 그대로
    back = read_document(path).doc
    assert back.경과보고 == "10월 교리 간식을 다음과 같이 사용하였습니다." and back.세부내역 == []


def test_filled_text_is_black(settings, tmp_path):
    """양식 칸의 안내 글씨는 빨강·파랑이지만 채운 글자는 같은 모양의 검정 복제본을 쓴다(양식 원래 글씨는 그대로)."""
    settings.계좌.예금주 = "홍길동"
    doc = settings.fill_contacts(build_report(settings, "본문 줄", 1_000, "행사 이름", "목적 문장",
                                              datetime(2026, 10, 5, 10), datetime(2026, 10, 6, 12), 40))
    path = render(doc, tmp_path / "r.hwpx")
    with zipfile.ZipFile(path) as z, zipfile.ZipFile(REPORT_TEMPLATE) as t:
        head, sec = z.read("Contents/header.xml").decode(), z.read("Contents/section0.xml").decode()
        base_sec = t.read("Contents/section0.xml").decode()
    ET.fromstring(head)
    color = dict(re.findall(r'<hh:charPr id="(\d+)"[^>]*?textColor="([^"]+)"', head))
    assert int(re.search(r'charProperties itemCnt="(\d+)"', head).group(1)) == len(color)
    runs = dict((t, ref) for ref, t in re.findall(r'<hp:run charPrIDRef="(\d+)"><hp:t>([^<]*)</hp:t>', sec))
    for text in ("본문 줄", "행사 이름", "목적 문장", "홍길동", "1,000원"):
        assert color[runs[text]] == "#000000", text
    assert "#FF0000" in color.values()  # 양식의 빨간 글자모양 자체는 남아 있다
    cells = read_cells(path)
    assert cells[REPORT_CELLS["일시"]][0].endswith("(  1박  2일)") and cells[REPORT_CELLS["인원"]][0].startswith(" 총 40명")


def test_fill_contacts_from_settings(settings, tmp_path):
    """연락처 = 설정, 계좌 칸별 = 문서별 덮어쓰기(빈 칸 제외) > 설정. 설정이 비면 문서도 빈칸."""
    s = settings.model_copy(deep=True)
    s.연락처.기안자 = "010-0000-0000"
    s.계좌 = AccountInfo(은행명="기본은행", 계좌번호="000-0", 예금주="기본예금주")
    out = s.fill_contacts(CHILDRENS_REQUEST)
    assert (out.기안자연락처, out.기안단체장연락처, out.은행명, out.계좌번호, out.예금주) == \
        ("010-0000-0000", "", "기본은행", "000-0", "기본예금주")
    doc = CHILDRENS_REQUEST.model_copy(update={"계좌_override": AccountInfo(예금주="덮어쓰기", 계좌번호="")})
    out = s.fill_contacts(doc)
    assert (out.은행명, out.계좌번호, out.예금주) == ("기본은행", "000-0", "덮어쓰기")
    assert CHILDRENS_REQUEST.기안자연락처 == ""  # 원본은 그대로
    cells = read_cells(render(out, tmp_path / "c.hwpx"))
    assert cells[REQUEST_CELLS["예금주"]][0].strip() == "덮어쓰기"
    blank = settings.model_copy(deep=True).fill_contacts(doc)  # 설정 비어 있음
    assert (blank.기안자연락처, blank.은행명, blank.계좌번호, blank.예금주) == ("", "", "", "덮어쓰기")
