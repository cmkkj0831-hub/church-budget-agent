"""영수증 이미지(여러 장) → 영수증별 {날짜, 상호, 금액, 품목 요약}.

이미지는 hwpx.receipts.prepare_image로 연다(HEIC, EXIF 회전, PDF 첫 쪽, 흰 여백 자르기).
금액을 읽지 못하면 null로 두고 확인 필요로 표시한다. 금액 판단(합계·대조)은 하지 않는다.
"""
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
- 영수증이 아니거나 여러 장이 겹쳐 하나를 고를 수 없으면 확인필요=true."""

SPEC = tool("submit_receipt", "영수증 1장의 값 제출", obj({
    "날짜": nullable(s("YYYY-MM-DD")),
    "상호": nullable(s()),
    "금액": nullable(i("결제 총액(원)")),
    "품목요약": s(),
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

    def to_receipt(self) -> Receipt:
        return Receipt(순번=self.순번, 이미지=self.파일, 날짜=self.날짜, 상호=self.상호,
                       금액=self.금액, 용도=self.품목요약)


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
    need = bool(out.get("확인필요")) or amount is None or day is None
    return ReceiptExtraction(n, str(path), day, out.get("상호") or "", amount, out.get("품목요약") or "",
                             need, " / ".join(r for r in reasons if r))


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
