"""LLM 사용량 기록(data/{연도}/usage.csv)과 비용 집계. 단가·환율은 설정(사용량단가)에서 받는다."""
from datetime import datetime
from pathlib import Path

from core.ledger import read_csv, write_csv
from core.models import UsagePricing

USAGE_COLUMNS = ["시각", "기능", "모델", "요청모델", "대체모델", "입력토큰", "출력토큰", "소요초", "환경"]

# 도구 이름 → 화면에 보일 기능 이름
FEATURES = {
    "submit_request_draft": "신청서 초안", "submit_receipt": "영수증 읽기", "submit_report_draft": "보고서 초안",
    "submit_issue_assessment": "이슈 분석", "submit_explanation": "이슈 설명", "submit_plan": "사업계획서 읽기",
}


def usage_row(info, env: str, when: datetime | None = None) -> dict:
    return {"시각": (when or datetime.now()).isoformat(timespec="seconds"), "기능": FEATURES.get(info.tool, info.tool),
            "모델": info.model, "요청모델": info.requested_model, "대체모델": "Y" if info.served_by_fallback else "",
            "입력토큰": info.input_tokens, "출력토큰": info.output_tokens, "소요초": round(info.seconds, 2), "환경": env}


def load_usage(path) -> list[dict]:
    path = Path(path)
    return read_csv(path) if path.exists() else []


def append_usage(path, rows: list[dict]) -> None:
    if rows:
        write_csv(path, USAGE_COLUMNS, load_usage(path) + rows)


def cost_krw(input_tokens: int, output_tokens: int, p: UsagePricing) -> float:
    usd = input_tokens / 1e6 * p.입력_USD_1M + output_tokens / 1e6 * p.출력_USD_1M
    return usd * p.환율_KRW_USD


def summarize(rows: list[dict], p: UsagePricing) -> list[dict]:
    """월·기능·환경별 호출 수, 토큰, 예상 비용(원)."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["시각"][:7], r["기능"], r["환경"])
        g = groups.setdefault(key, {"월": key[0], "기능": key[1], "환경": key[2], "호출": 0, "입력토큰": 0,
                                    "출력토큰": 0, "소요초": 0.0})
        g["호출"] += 1
        g["입력토큰"] += int(r["입력토큰"] or 0)
        g["출력토큰"] += int(r["출력토큰"] or 0)
        g["소요초"] += float(r["소요초"] or 0)
    out = sorted(groups.values(), key=lambda g: (g["월"], g["기능"], g["환경"]))
    for g in out:
        g["예상비용_원"] = round(cost_krw(g["입력토큰"], g["출력토큰"], p))
        g["소요초"] = round(g["소요초"], 1)
    return out


def annual_projection(rows: list[dict], p: UsagePricing, env: str = "prod") -> dict:
    """운영(prod) 사용량 기준 지금까지 비용과 12개월 환산 예상(사용한 달 수로 나눠 곱함)."""
    use = [r for r in rows if r["환경"] == env]
    months = {r["시각"][:7] for r in use}
    spent = cost_krw(sum(int(r["입력토큰"] or 0) for r in use), sum(int(r["출력토큰"] or 0) for r in use), p)
    per_month = spent / len(months) if months else 0
    return {"사용한 달": len(months), "지금까지(원)": round(spent), "월평균(원)": round(per_month),
            "연간 예상(원)": round(per_month * 12)}
