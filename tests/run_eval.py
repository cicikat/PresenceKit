"""
tests/run_eval.py — 离线 eval runner，不调用 LLM，只验证 prompt 层激活情况。

用法：
    python tests/run_eval.py

输出每条 case 的 layers_activated / token_estimate / tags，
供开发者确认动态裁剪是否按预期工作。
"""

import os
import sys
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 切换到 qq-st-bot 目录，与 main.py 保持一致
os.chdir(Path(__file__).parent.parent)
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.sandbox import init_paths
_paths = init_paths(mode="test")

from core import character_loader
from core.config_loader import get_config
from core.lore_engine import LoreEngine
from core.pipeline import Pipeline
from core.tag_rules import get_tags


def _make_empty_context() -> dict:
    return {
        "history":             [],
        "profile":             {},
        "relation":            {"role": "stranger"},
        "group_context":       [],
        "user_identity_text":  "",
        "event_search_result": "",
        "lore_entries":        [],
        "reminders":           [],
        "diary_context":       "",
        "episodic_result":     "",
    }


def main():
    eval_path = Path(__file__).parent / "eval_set.json"
    cases = json.loads(eval_path.read_text(encoding="utf-8"))

    cfg = get_config()
    char_filename = cfg.get("character", {}).get("default", "default.json")
    character = character_loader.load(char_filename)
    lore_engine = LoreEngine(character.world_book)
    lore_engine.load()
    pipeline = Pipeline(character, lore_engine)

    print("=" * 60)
    print(f"eval_set: {len(cases)} cases")
    print("=" * 60)

    for case in cases:
        cid      = case.get("id", "?")
        category = case.get("category", "")
        text     = case.get("input", "")

        tags = get_tags(text)
        ctx  = _make_empty_context()

        messages, debug_info = pipeline.build_prompt(
            user_id="eval_user",
            content=text,
            context=ctx,
            tags=tags,
        )

        layers  = debug_info.get("layers_activated", [])
        token_e = debug_info.get("token_estimate", 0)
        dtags   = debug_info.get("tags", [])

        print(f"[{cid}] {category}")
        print(f"  input  : {text!r}")
        print(f"  tags   : {sorted(dtags) or '(none)'}")
        print(f"  layers : {layers}")
        print(f"  tokens : ~{token_e}")
        print()

    _paths.cleanup()


if __name__ == "__main__":
    main()
