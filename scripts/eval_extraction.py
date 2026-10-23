"""영수증 추출 평가: 2026년/8월 신앙학교 영수증을 receipt_agent로 읽어 실제 보고서 금액과 비교한다.

    budget_env/bin/python scripts/eval_extraction.py [--truth 정답.csv] [--limit N] [--out 결과.csv]

정답 기준(기본): 8월 신앙학교 보고서(본당) '점심식사 440,000' + 9월 신앙학교 자체 보고서
'식대 350,500 · 준비물 구매 745,600 · 어린이 보험 24,850' = 1,560,950원.
영수증별 정답이 있으면 --truth(파일명,금액 CSV)로 장별 정확도도 본다.
※ 실제 API를 호출한다(영수증 수만큼 요청, 비용 발생). 결과 CSV는 out/에 둔다(커밋 제외).
"""
import argparse
import csv
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.receipt_agent import extract_receipts  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.paths import OUT_DIR, find_child, nfc, source_dir  # noqa: E402

REPORT_LINES = {  # (보고서, 항목) → 금액
    ("(집)2026-044 신앙학교(본당)", "점심식사"): 440_000,
    ("(집)2026-048 신앙학교(자체)", "식대"): 350_500,
    ("(집)2026-048 신앙학교(자체)", "준비물 구매"): 745_600,
    ("(집)2026-048 신앙학교(자체)", "어린이 보험"): 24_850,
}
SKIP = ("가입증명서", ".xlsx")  # 영수증이 아님(가입증명서는 개인정보 명단)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".pdf"}


def receipt_files() -> list[Path]:
    folder = find_child(find_child(source_dir(2026), "8월"), "신앙학교 영수증")
    files = [p for p in sorted(folder.iterdir(), key=lambda p: nfc(p.name))
             if p.suffix.lower() in IMAGE_EXT and not any(k in nfc(p.name) for k in SKIP)]
    return files


def subset_matches(amounts: dict[int, int], target: int, max_size: int = 6) -> list[tuple[int, ...]]:
    """합이 target인 영수증 번호 조합(작은 것부터, 최대 3개 반환)."""
    items = list(amounts.items())
    found = []
    for k in range(1, min(max_size, len(items)) + 1):
        for combo in combinations(items, k):
            if sum(a for _, a in combo) == target:
                found.append(tuple(n for n, _ in combo))
                if len(found) == 3:
                    return found
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", help="파일명,금액 CSV(영수증별 정답)")
    ap.add_argument("--limit", type=int, help="앞에서 N장만(시험용)")
    ap.add_argument("--out", default=str(OUT_DIR / "eval_extraction.csv"))
    args = ap.parse_args(argv)

    files = receipt_files()[: args.limit] if args.limit else receipt_files()
    ledger = Ledger.load()
    print(f"영수증 {len(files)}장 추출 중 (모델 {ledger.settings.모델})…")
    results = extract_receipts(files, ledger)

    truth = {}
    if args.truth:
        with open(args.truth, encoding="utf-8-sig") as f:
            truth = {nfc(r["파일명"]): int(r["금액"]) for r in csv.DictReader(f)}

    rows, correct, graded = [], 0, 0
    for r in results:
        name = nfc(Path(r.파일).name)
        expect = truth.get(name)
        ok = None if expect is None else (r.금액 == expect)
        if ok is not None:
            graded += 1
            correct += ok
        rows.append({"순번": r.순번, "파일명": name, "날짜": r.날짜 or "", "상호": r.상호,
                     "금액": "" if r.금액 is None else r.금액, "품목요약": r.품목요약,
                     "확인필요": "Y" if r.확인필요 else "", "확인사유": r.확인사유,
                     "정답": "" if expect is None else expect, "일치": "" if ok is None else ("O" if ok else "X")})
        print(f"{r.순번:>2}. {name:<34} {r.날짜 or '-':<10} {r.상호[:14]:<14} "
              f"{(f'{r.금액:,}' if r.금액 is not None else '읽지 못함'):>10}  {'확인필요 ' + r.확인사유 if r.확인필요 else ''}")

    read = {r.순번: r.금액 for r in results if r.금액 is not None}
    total, expected_total = sum(read.values()), sum(REPORT_LINES.values())
    print(f"\n추출 합계 {total:,}원 / 보고서 합계 {expected_total:,}원 (차이 {total - expected_total:+,}원)")
    print(f"금액 읽음 {len(read)}/{len(results)}장, 확인필요 {sum(r.확인필요 for r in results)}장")
    print("\n보고서 항목별로 합이 맞는 영수증 조합(있으면 추출 금액이 보고서와 일치한다는 뜻):")
    for (report, item), amount in REPORT_LINES.items():
        combos = subset_matches(read, amount)
        print(f"  {report} {item} {amount:,}원 → {combos or '없음'}")
    if graded:
        print(f"\n영수증별 금액 정확도 {correct}/{graded} = {correct / graded:.0%}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
