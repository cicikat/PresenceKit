#!/usr/bin/env python3
"""
scripts/audit_hidden_state.py
Audit tool for UserHiddenState — shows field values, last_update_source
distribution, and optional before/after comparison to prove whether
reality-side chat writes have any effect.

Usage:
    # Dump live state + source distribution
    python scripts/audit_hidden_state.py

    # Save a snapshot before chatting
    python scripts/audit_hidden_state.py --save snapshots/before.json

    # Compare saved snapshot to current live state
    python scripts/audit_hidden_state.py --snapshot snapshots/before.json

    # Diff two saved snapshots (no live load)
    python scripts/audit_hidden_state.py \\
        --snapshot snapshots/before.json \\
        --snapshot2 snapshots/after.json

    # Override uid / char_id
    python scripts/audit_hidden_state.py --uid 12345 --char yexuan
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Config helpers ────────────────────────────────────────────────────────────

def _load_config_uid() -> str:
    try:
        import yaml  # type: ignore[import]
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        return str(cfg.get("scheduler", {}).get("owner_id", "owner"))
    except Exception as exc:
        print(f"[warn] Could not read owner_id from config.yaml: {exc}", file=sys.stderr)
        return "owner"


def _load_active_char() -> str:
    try:
        from core.sandbox import get_paths
        p = get_paths().active_prompt_assets()
        data = json.loads(p.read_text(encoding="utf-8"))
        char_id = (data.get("active_character") or "").strip()
        if not char_id:
            raise ValueError("active_character is empty")
        return char_id
    except Exception as exc:
        print(f"[warn] Could not read active_character: {exc}. Defaulting to 'yexuan'.", file=sys.stderr)
        return "yexuan"


# ── State loading ─────────────────────────────────────────────────────────────

def _load_live_state(uid: str, char_id: str) -> dict[str, Any]:
    from core.memory.user_hidden_state_store import load_hidden_state
    from core.memory.user_hidden_state import to_dict
    state = load_hidden_state(uid, char_id=char_id)
    return to_dict(state)


def _load_snapshot_file(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return ts


def _src(scalar: dict) -> str:
    return scalar.get("last_update_source", "unknown")


def _print_scalar(
    label: str,
    scalar: dict,
    baseline_val: float | None = None,
) -> None:
    val = scalar.get("value", 0.0)
    src = _src(scalar)
    updated = _fmt_ts(scalar.get("last_updated"))
    delta_str = ""
    if baseline_val is not None:
        delta = val - baseline_val
        sign = "+" if delta >= 0 else ""
        arrow = "↑" if delta > 0.05 else ("↓" if delta < -0.05 else "→")
        delta_str = f"  Δ={sign}{delta:.2f} {arrow}"
    print(f"    {label:<22}  {val:7.2f}   src={src:<26}  updated={updated}{delta_str}")


# ── Single-state display ──────────────────────────────────────────────────────

def _print_state(raw: dict, label: str = "Live") -> None:
    print(f"\n{'═' * 70}")
    print(f"  {label}  —  schema_version={raw.get('schema_version', '?')}")
    print(f"  last_decay_tick : {_fmt_ts(raw.get('last_decay_tick'))}")
    print(f"{'─' * 70}")

    sens = raw.get("sensitivity", {})
    sens_baseline = sens.get("baseline", {})
    sens_current = sens.get("current", {})
    print("\n  [sensitivity]")
    _print_scalar("baseline", sens_baseline)
    _print_scalar(
        "current",
        sens_current,
        baseline_val=sens_baseline.get("value"),
    )

    touch = raw.get("touch_need", {})
    touch_baseline = touch.get("baseline", {})
    touch_deficit = touch.get("deficit", {})
    print("\n  [touch_need]")
    _print_scalar("baseline", touch_baseline)
    _print_scalar(
        "deficit (Δ from 0)",
        touch_deficit,
        baseline_val=0.0,
    )

    ease = raw.get("embodied_ease", {})
    ease_val = ease.get("value", 50.0)
    print("\n  [embodied_ease]")
    _print_scalar("value (Δ from center 50)", ease, baseline_val=50.0)

    bm = raw.get("body_memory", {})
    entries = bm.get("entries", [])
    max_e = bm.get("max_entries", 32)
    print(f"\n  [body_memory]  {len(entries)}/{max_e} entries")
    if entries:
        for e in sorted(entries, key=lambda x: x.get("weight", 0), reverse=True):
            cue = e.get("cue", "?")
            tag = e.get("response_tag", "?")
            w = e.get("weight", 0)
            print(f"    cue={cue!r:<32}  response_tag={tag!r:<22}  weight={w:.4f}")
    else:
        print("    (no entries)")
    print()


# ── Source distribution ───────────────────────────────────────────────────────

def _source_distribution(raw: dict) -> dict[str, int]:
    sources: dict[str, int] = {}

    def _tally(d: dict) -> None:
        src = d.get("last_update_source")
        if src:
            sources[src] = sources.get(src, 0) + 1

    sens = raw.get("sensitivity", {})
    _tally(sens.get("baseline", {}))
    _tally(sens.get("current", {}))
    touch = raw.get("touch_need", {})
    _tally(touch.get("baseline", {}))
    _tally(touch.get("deficit", {}))
    _tally(raw.get("embodied_ease", {}))
    return sources


def _print_source_distribution(raw: dict) -> None:
    dist = _source_distribution(raw)
    total = sum(dist.values())
    print(f"  Source distribution ({total} tracked fields):")
    for src, count in sorted(dist.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        print(f"    {src:<32}  {count:2d}x  ({pct:.0f}%)")

    reality_sources = {k for k in dist if k in ("reality_behavior",)}
    all_passive = not reality_sources and all(
        k in ("time_decay", "init", "consolidation", "dream_afterglow", "dream_impression", "dream_body_event")
        for k in dist
    )
    if all_passive:
        print()
        print("  [!] CONCLUSION: 所有字段均由被动衰减/初始化驱动。")
        print("      现实对话从未写入 hidden_state。")
        print("      写入链已接线（pipeline.post_process_slow → process_reality_turn，Brief 88）；")
        print("      若长期无 reality_behavior 来源，检查是否从未触发判定条件或写入被拒。")
    elif reality_sources:
        print()
        print("  [OK] 检测到 reality_behavior 写入记录 — 现实侧写入链已激活。")


# ── Diff between two snapshots ────────────────────────────────────────────────

def _print_diff(before: dict, after: dict, label_b: str = "Before", label_a: str = "After") -> None:
    print(f"\n{'═' * 70}")
    print(f"  Diff  {label_b!r}  →  {label_a!r}")
    print(f"{'─' * 70}")

    def _sdiff(field: str, d0: dict, d1: dict) -> None:
        v0 = d0.get("value", 0.0)
        v1 = d1.get("value", 0.0)
        delta = v1 - v0
        src0 = d0.get("last_update_source", "?")
        src1 = d1.get("last_update_source", "?")
        src_note = (
            f"  source unchanged ({src1})"
            if src0 == src1
            else f"  source: {src0} → {src1}"
        )
        changed = "CHANGED" if abs(delta) > 0.001 else "no change"
        sign = "+" if delta >= 0 else ""
        print(f"    {field:<32}  {v0:.3f} → {v1:.3f}  ({sign}{delta:.3f})  {changed}{src_note}")

    sens0 = before.get("sensitivity", {})
    sens1 = after.get("sensitivity", {})
    _sdiff("sensitivity.baseline", sens0.get("baseline", {}), sens1.get("baseline", {}))
    _sdiff("sensitivity.current", sens0.get("current", {}), sens1.get("current", {}))

    touch0 = before.get("touch_need", {})
    touch1 = after.get("touch_need", {})
    _sdiff("touch_need.baseline", touch0.get("baseline", {}), touch1.get("baseline", {}))
    _sdiff("touch_need.deficit", touch0.get("deficit", {}), touch1.get("deficit", {}))

    _sdiff("embodied_ease", before.get("embodied_ease", {}), after.get("embodied_ease", {}))

    bm0 = len(before.get("body_memory", {}).get("entries", []))
    bm1 = len(after.get("body_memory", {}).get("entries", []))
    bm_note = "no change" if bm0 == bm1 else "CHANGED"
    print(f"    {'body_memory.entries (count)':<32}  {bm0} → {bm1}  {bm_note}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit UserHiddenState: dump fields, source distribution, and diffs."
    )
    parser.add_argument("--uid", help="User ID (default: config.yaml owner_id)")
    parser.add_argument("--char", dest="char_id", help="Character ID (default: active_character)")
    parser.add_argument(
        "--snapshot",
        metavar="FILE",
        help="Previously saved JSON snapshot to compare against current live state",
    )
    parser.add_argument(
        "--snapshot2",
        metavar="FILE",
        help="Second snapshot for snapshot-vs-snapshot diff (skips live load)",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Save the current live state to FILE for later comparison",
    )
    args = parser.parse_args()

    uid = args.uid or _load_config_uid()
    char_id = args.char_id or _load_active_char()

    print(f"\n{'━' * 70}")
    print(f"  UserHiddenState Audit")
    print(f"  uid={uid!r}   char_id={char_id!r}")
    print(f"  Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'━' * 70}")

    # Snapshot-vs-snapshot diff (no live load)
    if args.snapshot2:
        if not args.snapshot:
            parser.error("--snapshot2 requires --snapshot")
        snap1 = _load_snapshot_file(args.snapshot)
        snap2 = _load_snapshot_file(args.snapshot2)
        _print_state(snap1, f"Snapshot 1: {args.snapshot}")
        _print_source_distribution(snap1)
        _print_state(snap2, f"Snapshot 2: {args.snapshot2}")
        _print_source_distribution(snap2)
        _print_diff(snap1, snap2, label_b=args.snapshot, label_a=args.snapshot2)
        return

    # Live state
    live = _load_live_state(uid, char_id)
    _print_state(live, "Live state")
    _print_source_distribution(live)

    # Save snapshot
    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  Saved snapshot → {out}")

    # Compare with saved snapshot
    if args.snapshot:
        snap = _load_snapshot_file(args.snapshot)
        _print_state(snap, f"Saved snapshot: {args.snapshot}")
        _print_source_distribution(snap)
        _print_diff(snap, live, label_b=args.snapshot, label_a="live")


if __name__ == "__main__":
    main()
