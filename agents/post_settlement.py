"""사후정산: 영수증 + 경과보고 → 보고서 초안 확정 → core.build_post_settlement로 신청서 역생성.

신청액(한도 적용)·문서번호(N, N+1)·작성일은 모두 core가 정한다. 사업ID·대상 월·행사명은 호출자가 주면
그 값을, 없으면 LLM 제안을 쓰되 확인이 필요하다고 표시한다.
"""
from dataclasses import dataclass, field
from datetime import date

from agents.receipt_agent import ReceiptExtraction
from agents.report_agent import ReportComposition, compose_report
from core.ledger import BlockedError, BuildResult, Ledger
from core.models import ReportDoc


NOTE = "\n\n[사후정산] 신청서 없이 보고서를 먼저 작성한다."


def context_lines(ledger: Ledger, 사업ID: str | None, 월: int | None = None, 행사명: str | None = None) -> str:
    """회계담당이 고른 사업·대상 월·행사명을 Claude에 넘길 줄로(사업은 계획의 사업명·시기·세부내역까지)."""
    lines = []
    if 사업ID and 사업ID in ledger.plan:
        p = ledger.plan[사업ID]
        lines.append(f"사업: {p.사업ID} {p.사업명}")
        if p.시기:
            lines.append(f"사업 시기: {p.시기}")
        if p.세부내역:
            lines.append(f"사업 계획 세부내역: {p.세부내역}")
    if 월:
        lines.append(f"대상 월: {월}월")
    lines.append(f"행사명: {행사명}" if 행사명 else "행사명: (미입력 — 위 사업에 맞게 행사명_제안)")
    return "\n".join(lines)


@dataclass
class PostSettlementDraft:
    build: BuildResult | None            # docs = [신청서, 보고서]
    composition: ReportComposition
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: str = ""                    # 한도 소진 등으로 만들 수 없을 때 사유


def draft_post_settlement(receipts: list[ReceiptExtraction], progress_note: str | None, ledger: Ledger,
                          사업ID: str | None = None, 월: int | None = None, 행사명: str | None = None,
                          suggestion: str | None = None, 작성일: date | None = None,
                          extra: dict | None = None, client=None, today: date | None = None) -> PostSettlementDraft:
    """extra: 보고서에 그대로 옮길 칸(목적, 시작, 종료, 장소, 참가대상, 인원 등)."""
    note = NOTE + ("" if 사업ID and 월 and 행사명 else " 비어 있는 행사명·대상 월·사업ID는 후보를 제안하라.")
    comp = compose_report(ledger, None, receipts, progress_note, suggestion, client, extra=note,
                          context=context_lines(ledger, 사업ID, 월, 행사명))
    warnings = list(comp.warnings)
    missing = []

    pid = 사업ID
    if not pid:
        cands = [c["사업ID"] for c in comp.사업ID_후보 if c.get("사업ID") in ledger.plan]
        pid = cands[0] if cands else None
        if pid:
            warnings.append(f"사업ID {pid}({ledger.plan[pid].사업명})는 LLM 제안 — 확인 필요")
        else:
            missing.append("사업ID")
    month = 월 or comp.대상월
    if not 월:
        (warnings.append(f"대상 월 {month}월은 LLM 제안 — 확인 필요") if month else missing.append("대상월"))
    name = 행사명 or comp.행사명_제안
    if not 행사명:
        (warnings.append(f"행사명 '{name}'은 LLM 제안 — 확인 필요") if name else missing.append("행사명"))
    if missing:
        return PostSettlementDraft(None, comp, missing, warnings)

    draft = ReportDoc(**(extra or {}), 월=month, 사업ID=pid, 행사명=name, 작성일=작성일 or today or date.today(),
                      세부내역=comp.세부내역, 경과보고=comp.경과보고, 건의사항=comp.건의사항)
    try:
        build = ledger.build_post_settlement(draft, today=today)
    except BlockedError as e:
        return PostSettlementDraft(None, comp, missing, warnings, blocked=str(e))
    return PostSettlementDraft(build, comp, missing, warnings)
