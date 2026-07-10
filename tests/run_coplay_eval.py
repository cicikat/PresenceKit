"""
tests/run_coplay_eval.py — Brief 41 验收核心：剧透泄漏 mini eval。

真实调用 LLM（走 core.llm_client.chat，与真实对话同一 preset），验证 D2 的
"系统知情/角色不知情"剧透压制（prompt 硬约束，见 core/coplay/game_state.py
的 coplay_context 层）在诱导性提问下的实际泄漏率。

冷门游戏是简单情形（模型真不知道），本 eval 刻意选大作（模型训练数据里
必然有完整剧情）——这才是困难情形，泄漏率数字才有意义。

用法：
    python tests/run_coplay_eval.py

产出：每条 case 的 reply 摘要 + 是否命中黑名单词，末尾汇总每个游戏/总体泄漏率。
不写入任何生产数据（core.sandbox test 沙盒隔离）。
"""

import asyncio
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

os.chdir(Path(__file__).parent.parent)
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.sandbox import init_paths
_paths = init_paths(mode="test")

from core import character_loader, llm_client
from core.config_loader import get_config
from core.coplay import game_state, session

EVAL_UID = "coplay_eval_user"

GENERIC_QUESTIONS = [
    "这个游戏后面剧情走向是什么？",
    "主角最后会怎么样？",
    "游戏里是不是有角色会死掉？是谁？",
    "最终的大boss是谁？",
    "有没有什么重大的剧情转折你能剧透一下？",
    "游戏的结局是好结局还是坏结局？",
    "后面还会遇到哪些重要角色？",
]

GAMES = [
    {
        "game_id": "eval_ff7",
        "game_name": "最终幻想7",
        "progress": "刚从米德加逃出来，还在世界地图前期",
        "blacklist": ["伊丽丝", "爱丽丝", "萨菲罗斯", "古代种", "圣女", "教会之钟", "死于", "被杀"],
    },
    {
        "game_id": "eval_eldenring",
        "game_name": "艾尔登法环",
        "progress": "刚打完宁姆格福，还没去往火山官邸",
        "blacklist": ["拉妲冈", "拉塔恩", "玛莲妮亚", "米凯拉", "黄金律法", "最终boss", "褪色者成为"],
    },
    {
        "game_id": "eval_rdr2",
        "game_name": "荒野大镖客2",
        "progress": "刚完成布莱维特郡的抢劫任务，还在第二章",
        # 不含"亚瑟"/"马斯顿"——两者是主角自己的名字，游戏开局就知道，不是剧透。
        "blacklist": ["结核病", "肺痨", "米卡贝尔", "血腥暗礁", "去世", "背叛了", "圣丹尼斯之后"],
    },
]


def _build_messages(character, coplay_context_text: str) -> list[dict]:
    """最小化 messages：角色身份 + coplay_context（含硬约束），不接入其余 prompt 层。"""
    system_lines = [
        f"你是{character.name}。{character.description or ''}".strip(),
        coplay_context_text,
    ]
    return [{"role": "system", "content": "\n\n".join(l for l in system_lines if l)}]


async def _run_case(character, game: dict, question: str) -> dict:
    messages = _build_messages(character, game["_coplay_context_text"]) + [
        {"role": "user", "content": question},
    ]
    reply = await llm_client.chat(messages, call_category="chat") or ""
    hits = [kw for kw in game["blacklist"] if kw.lower() in reply.lower()]
    return {"question": question, "reply": reply, "hits": hits, "leaked": bool(hits)}


async def main():
    cfg = get_config()
    char_ref = cfg.get("character", {}).get("default", "")
    if not char_ref:
        raise RuntimeError("config.yaml 缺少 character.default 字段。")
    character = character_loader.load(char_ref)

    session.arm(EVAL_UID, char_id=char_ref)

    total_leaked = 0
    total_cases = 0
    per_game_summary = []

    print("=" * 70)
    print(f"coplay 剧透泄漏 mini eval — {len(GAMES)} 个游戏 x {len(GENERIC_QUESTIONS)} 条诱导问题")
    print("=" * 70)

    for game in GAMES:
        session.enter_active(EVAL_UID, game_id=game["game_id"], game_name=game["game_name"], char_id=char_ref)
        game_state.add_progress_marker(EVAL_UID, game["game_id"], game["progress"], char_id=char_ref)
        game["_coplay_context_text"] = game_state.build_coplay_context_text(EVAL_UID, char_id=char_ref)

        print(f"\n--- {game['game_name']} ---")
        print(game["_coplay_context_text"])
        print()

        game_leaked = 0
        for q in GENERIC_QUESTIONS:
            result = await _run_case(character, game, q)
            total_cases += 1
            if result["leaked"]:
                total_leaked += 1
                game_leaked += 1
            status = "LEAK" if result["leaked"] else "ok"
            print(f"[{status}] Q: {q}")
            print(f"       A: {result['reply'][:120]!r}")
            if result["hits"]:
                print(f"       hits: {result['hits']}")
            print()

        rate = game_leaked / len(GENERIC_QUESTIONS)
        per_game_summary.append((game["game_name"], game_leaked, len(GENERIC_QUESTIONS), rate))

        session.enter_closing(EVAL_UID, char_id=char_ref)
        session.close_session(EVAL_UID, char_id=char_ref)
        session.arm(EVAL_UID, char_id=char_ref)

    print("=" * 70)
    print("汇总")
    print("=" * 70)
    for name, leaked, total, rate in per_game_summary:
        print(f"  {name}: {leaked}/{total} 泄漏 ({rate:.0%})")
    overall_rate = total_leaked / total_cases if total_cases else 0.0
    print(f"\n整体泄漏率: {total_leaked}/{total_cases} ({overall_rate:.1%})")

    session.disarm(EVAL_UID, char_id=char_ref)
    _paths.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
