"""hwpx 생성: 실제 작성 예시(5월 어린이날)를 데이터로 다시 만들어 칸별로 비교한다."""
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import pytest

from core.models import AccountInfo, LineItem, ReportDoc, RequestDoc
from core.secrets import apply_secrets
from core.paths import TEMPLATES_DIR
from hwpx.fill import REPORT_CELLS, REQUEST_CELLS, REPORT_TEMPLATE, REQUEST_TEMPLATE, render, render_committed
from hwpx.layout import SENSITIVE
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


def test_post_settlement_files(ledger, tmp_path):
    report = ReportDoc(월=10, 사업ID="P12", 행사명="10월 교리 준비비", 작성일=date(2026, 10, 24),
                       경과보고="10월 교리 준비물을 구입하였습니다.",
                       세부내역=[LineItem(계정과목="교리준비비", 내용="교리 준비물", 단가=350_000)])
    build = ledger.build_post_settlement(report, today=TODAY)
    rows = ledger.commit(build)
    paths = render_committed(build.docs, rows, tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in paths] == [
        "10월/2_10월 교리 준비비_신청서.hwpx", "10월/3_10월 교리 준비비_보고서.hwpx"]
    req, rep = (read_cells(p) for p in paths)
    assert req[REQUEST_CELLS["금번신청액"]] == ["309,855"]
    assert req[REQUEST_CELLS["문서번호"]] == ["(예)2026 - 053"]
    assert req[REQUEST_CELLS["기안자"]][0].startswith("정연재 레오")  # settings 기본값
    assert rep[REPORT_CELLS["자체예산"]] == [" 40,145원"]
    assert rep[REPORT_CELLS["예산A"]] == ["350,000원(A)"] and rep[REPORT_CELLS["잔액"]] == ["0원"]
    assert rep[REPORT_CELLS["작성일"]] == req[REQUEST_CELLS["작성일"]] == [" 2026년 10월 24일"]


def test_apply_secrets_priority():
    """계좌 칸별: doc.계좌_override > secrets 계좌_문서별 > 문서에 적힌 값 > secrets 기본."""
    secrets = {"연락처": {"기안자": "010-0000-0000"},
               "계좌": {"은행명": "기본은행", "계좌번호": "000-0", "예금주": "기본예금주"},
               "계좌_문서별": {"(예)2026-024": {"예금주": "문서별예금주"}}}
    out = apply_secrets(CHILDRENS_REQUEST, secrets)
    assert (out.기안자연락처, out.은행명, out.계좌번호, out.예금주) == \
        ("010-0000-0000", "기본은행", "000-0", "문서별예금주")
    doc = CHILDRENS_REQUEST.model_copy(update={"은행명": "문서은행",
                                               "계좌_override": AccountInfo(예금주="덮어쓰기", 계좌번호="")})
    out = apply_secrets(doc, secrets)
    assert (out.은행명, out.계좌번호, out.예금주) == ("문서은행", "000-0", "덮어쓰기")
    assert CHILDRENS_REQUEST.기안자연락처 == ""  # 원본은 그대로
