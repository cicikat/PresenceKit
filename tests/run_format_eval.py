"""Brief 73 换行合规率 eval 的离线 runner 与可选在线配对 A/B。

离线：
    python tests/run_format_eval.py

在线（真实 Pipeline；char_id 必须显式给出）：
    python tests/run_format_eval.py --online --n=50 --char-id=<id>
    python tests/run_format_eval.py --online --n=50 --char-id=<id> --enforce=on
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from core.memory.short_term import DEFAULT_SEGMENT_MIN_LEN  # noqa: E402
from tests.format_eval import engine  # noqa: E402
from tests.format_eval.scorer import aggregate_scores  # noqa: E402


def _percent(value: float) -> str:
    return f"{value:.1%}"


def run_offline() -> int:
    cases = engine.load_cases()
    if len(cases) < 4:
        print(f"[FAIL] format case count {len(cases)} < minimum 4")
        return 1
    failures = 0
    scores = []
    for case in cases:
        result = engine.run_case(case)
        scores.append(result)
        problems = engine.check_expectations(case, result)
        if problems:
            failures += 1
            print(f"[FAIL] {case['id']}: {'; '.join(problems)}")
        else:
            print(f"[ok]   {case['id']}")

    print("\nmode    compliant       rate    avg paragraphs    unsegmented")
    print("-" * 68)
    for mode in ("strict", "loose"):
        metrics = aggregate_scores(scores, mode=mode)
        print(
            f"{mode:7s} {metrics.compliant_count:3d}/{metrics.total:<3d} "
            f"{_percent(metrics.compliance_rate):>9s} "
            f"{metrics.average_paragraph_count:17.2f} "
            f"{metrics.unsegmented_count:3d}/{metrics.total:<3d} "
            f"({_percent(metrics.unsegmented_ratio)})"
        )
    print(f"\nformat eval: {len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


def run_online(*, n: int, char_id: str, min_len: int, enforce: str) -> int:
    print("evaluation_mode=report_only; no blocking quality threshold configured")
    replies = engine.generate_online_replies_sync(n=n, char_id=char_id)
    rows = engine.build_online_rows(replies, min_len=min_len)
    selected = rows if enforce == "both" else [row for row in rows if row.enforce == enforce]

    print(f"\nonline format eval: n={n}, char_id={char_id}, min_len={min_len}")
    print("enforce    compliant       rate    avg paragraphs    fallback triggered")
    print("-" * 78)
    for row in selected:
        metrics = row.metrics
        print(
            f"{row.enforce:7s} {metrics.compliant_count:3d}/{metrics.total:<3d} "
            f"{_percent(metrics.compliance_rate):>9s} "
            f"{metrics.average_paragraph_count:17.2f} "
            f"{row.fallback_trigger_count:3d}/{metrics.total:<3d} "
            f"({_percent(row.fallback_trigger_ratio)})"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="换行合规率离线 eval / 在线 A/B")
    parser.add_argument("--online", action="store_true", help="调用真实 Pipeline 生成回复")
    parser.add_argument("--n", type=int, default=50, help="在线生成条数（默认 50）")
    parser.add_argument("--char-id", help="在线评测角色 id；无默认值，缺失即失败")
    parser.add_argument(
        "--enforce",
        choices=("off", "on", "both"),
        default="both",
        help="在线输出 off、on 或两行配对对比（默认 both）",
    )
    parser.add_argument("--min-len", type=int, default=DEFAULT_SEGMENT_MIN_LEN)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.online:
        return run_offline()
    if not args.char_id:
        raise SystemExit("--online requires explicit --char-id (no default fallback)")
    if args.n < 1:
        raise SystemExit("--n must be >= 1")
    return run_online(
        n=args.n,
        char_id=args.char_id,
        min_len=max(1, args.min_len),
        enforce=args.enforce,
    )


if __name__ == "__main__":
    raise SystemExit(main())
