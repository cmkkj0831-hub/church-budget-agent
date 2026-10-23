"""영수증 이미지(여러 장) → 영수증별 {날짜, 상호, 금액, 품목 요약}.

이미지는 hwpx.receipts.prepare_image로 연다(HEIC, EXIF 회전, PDF 첫 쪽, 흰 여백 자르기).
금액을 읽지 못하면 null로 두고 확인 필요로 표시한다. 금액 판단(합계·대조)은 하지 않는다.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agents.llm import CallInfo, call, i, image_block, jpeg_bytes, make_client, nullable, obj, s, tool
from core.models import Receipt
from hwpx.receipts import prepare_image

SYSTEM = """너는 영수증·카드매출전표·이체확인증 이미지에서 회계에 필요한 값을 그대로 옮겨 적는다.
- 날짜: 거래일(YYYY-MM-DD). 연도가 안 보이면 {year}년으로 본다.
- 상호: 가맹점명(지점명 포함).
- 금액: 실제 결제된 총액(원, 정수). 부가세 별도 금액이나 공급가액이 아니라 합계 금액이다.
  흐리거나 잘려서 확실히 읽을 수 없으면 null로 두고 확인필요=true, 사유를 적는다. 추측해서 적지 않는다.
- 품목요약: 산 물건·용도를 10자 안팎으로(예: "점심 식사", "게임 선물", "보험료").
- 가맹점명_원문: 영수증에 '가맹점명'으로 찍힌 글자 그대로(결제대행사 이름이어도 고치지 않는다). 상호는 같은 값을 쓰되,
  영수증 안에 실제 가게 이름이 따로 보이면 그 이름.
- 승인번호: 카드 승인번호(숫자 그대로). 없으면 빈 문자열.
- 결제수단: 신용카드, 체크카드, 계좌이체, 현금, 간편결제 중 보이는 것. 모르면 빈 문자열.
- 영수증이 아니거나 여러 장이 겹쳐 하나를 고를 수 없으면 확인필요=true."""

# 가맹점명이 결제대행사(PG)로 찍히는 경우 — 실제 상호는 사용자 설명(progress_note)으로 확인
PG_PATTERNS = (r"나이스\s*정보통신", r"NICE\s*정보통신", r"엔에이치엔\s*케이씨피", r"NHN\s*KCP", r"한국사이버결제",
               r"KG\s*이니시스", r"케이지\s*이니시스", r"이니시스", r"토스\s*페이먼츠", r"다날", r"한국정보통신", r"KICC",
               r"스마트로", r"KSNET", r"케이에스넷", r"갤럭시아", r"페이레터", r"헥토파이낸셜", r"세틀뱅크", r"카카오페이",
               r"네이버\s*파이낸셜", r"(주)?\s*페이코")
PG_RE = re.compile("|".join(PG_PATTERNS), re.I)


def is_pg(name: str) -> bool:
    return bool(PG_RE.search(name or ""))

SPEC = tool("submit_receipt", "영수증 1장의 값 제출", obj({
    "날짜": nullable(s("YYYY-MM-DD")),
    "상호": nullable(s()),
    "금액": nullable(i("결제 총액(원)")),
    "품목요약": s(),
    "가맹점명_원문": s("영수증의 가맹점명 그대로, 없으면 빈 문자열"),
    "승인번호": s("없으면 빈 문자열"),
    "결제수단": s("없으면 빈 문자열"),
    "확인필요": {"type": "boolean"},
    "확인사유": s("확인필요가 아니면 빈 문자열"),
}))


@dataclass
class ReceiptExtraction:
    순번: int
    파일: str
    날짜: date | None
    상호: str
    금액: int | None
    품목요약: str
    확인필요: bool
    확인사유: str = ""
    call: CallInfo | None = None  # 이 영수증을 읽은 호출 기록
    가맹점명_원문: str = ""
    승인번호: str = ""
    결제수단: str = ""
    대행사표기: bool = False       # 가맹점명이 결제대행사 → 실제 상호 확인 필요
    상호_사용자: str = ""          # 사용자 설명(progress_note·화면)에서 받은 실제 상호. 있으면 우선

    @property
    def 상호_확정(self) -> str:
        return self.상호_사용자 or self.상호

    def to_receipt(self) -> Receipt:
        return Receipt(순번=self.순번, 이미지=self.파일, 날짜=self.날짜, 상호=self.상호_확정,
                       금액=self.금액, 용도=self.품목요약)


def duplicate_warnings(receipts: list[ReceiptExtraction]) -> list[str]:
    """같은 승인번호·금액·날짜인 영수증(중복 제출 의심)."""
    groups: dict[tuple, list[ReceiptExtraction]] = {}
    for r in receipts:
        approval = re.sub(r"\D", "", r.승인번호 or "")
        if approval and r.금액 is not None and r.날짜:
            groups.setdefault((approval, r.금액, r.날짜), []).append(r)
    return [f"중복 의심: 영수증 {', '.join(f'{r.순번}번({Path(r.파일).name})' for r in rs)} — "
            f"승인번호 {key[0]}, {key[1]:,}원, {key[2].isoformat()}"
            for key, rs in groups.items() if len(rs) > 1]


def to_extraction(n: int, path, out: dict) -> ReceiptExtraction:
    try:
        day = date.fromisoformat(out["날짜"]) if out.get("날짜") else None
    except ValueError:
        day = None
    amount = out.get("금액")
    reasons = [out.get("확인사유") or ""]
    if amount is None:
        reasons.append("금액을 읽지 못함")
    elif amount <= 0:
        reasons.append(f"금액 {amount}원")
        amount = None
    if day is None:
        reasons.append("날짜를 읽지 못함")
    raw = out.get("가맹점명_원문") or ""
    store = out.get("상호") or raw
    pg = is_pg(raw) or is_pg(store)
    if pg:
        reasons.append(f"대행사 표기({raw or store}) — 실제 상호 확인")
    need = bool(out.get("확인필요")) or amount is None or day is None
    return ReceiptExtraction(n, str(path), day, store, amount, out.get("품목요약") or "",
                             need, " / ".join(r for r in reasons if r), 가맹점명_원문=raw,
                             승인번호=(out.get("승인번호") or "").strip(), 결제수단=out.get("결제수단") or "",
                             대행사표기=pg)


def extract_receipts(paths: list, ledger, client=None) -> list[ReceiptExtraction]:
    """영수증마다 한 번씩 호출한다(장별 결과를 섞지 않기 위해). 순번은 입력 순서."""
    client = client or make_client()
    system = SYSTEM.format(year=ledger.settings.기본정보.회계연도)
    out = []
    for n, path in enumerate(paths, 1):
        image = prepare_image(Path(path))
        content = [image_block(jpeg_bytes(image)), {"type": "text", "text": f"영수증 {n}번 ({Path(path).name})"}]
        result = call(ledger, client, system, content, SPEC)
        ext = to_extraction(n, path, result.input)
        ext.call = result.info
        out.append(ext)
    return out
