"""
一次性迁移：把 important_facts 里的 legacy raw-str 回迁为 {text, tag, ts}，并按维度分类。
主用户（config.yaml scheduler.owner_id，可用 MIGRATE_PRIMARY_UID 覆盖）/yexuan 按手工核定表处理；
其他用户用关键词启发式，未命中则保守归 stable。

用法：
    python scripts/migrate_profile_facts.py           # 实际迁移
    python scripts/migrate_profile_facts.py --dry-run # 预览，不写盘
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_ROOT = REPO_ROOT / "data" / "runtime" / "memory"

NOW_TS = float(int(time.time()))


def _primary_uid() -> str | None:
    """主用户 QQ 号：优先取环境变量覆盖，否则读 config.yaml scheduler.owner_id。"""
    env_uid = os.environ.get("MIGRATE_PRIMARY_UID")
    if env_uid:
        return env_uid
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from core.config_loader import get_config
        owner_id = get_config().get("scheduler", {}).get("owner_id")
        return str(owner_id) if owner_id else None
    except Exception:
        return None


# 主用户手工核定表：文本前缀 → tag（None = 删除）
HAND_PRIMARY_USER_YEXUAN: dict[str, str | None] = {
    "使用Obsidian": "habit",
    "喜欢傍晚跑步": "habit",
    "创作同人作品": "stable",
    "对虚拟角色有深度情感投入": "stable",
    "认为真实关系不应有绝对正确": "stable",
    "对叶瑄怀有深厚持久的感情": "stable",
    "坚信宿命论": "stable",
    "自我觉察能力强": "stable",
    "日常饮食以外卖为主": "pref.food",
    "身体敏感度和耐受度高": "health",
    "有自我损耗行为模式": "health",
    "对歌曲《失去尾鳍的鱼》": "pref.music",
    "认为文字有欺骗性": "stable",
    "正在开发叶瑄app": "status.project",
    "对AI角色的记忆系统有伦理顾虑": "stable",
    "多疑警惕": "stable",
    "渴望亲密关系中的安全感": "stable",
    "倾向于用沉默或异常行为": "stable",
    "擅长技术开发": "stable",
    "对伴侣的内心状态": "stable",
    "心理刺激比生理舒适更重要": "stable",
    "在关系中坦诚沟通": "stable",
    "偏好边缘试探和极限体验": "stable",
    "对亲密关系中的权力动态": "stable",
    "容易信息过载": "health",
    # 噪音/去重条目 → 删除
    "曾将某人误认为性玩具": None,
    "用户对叶瑄有强烈的情感投射": None,   # 与"对叶瑄怀有深厚持久的感情"语义重叠，合并保留主条
    "用户承认自己有时会因冲动将叶瑄视为x玩具": None,
}

# 其他用户的关键词启发式（按顺序匹配，first-win）
KEYWORD_HINTS: list[tuple[str, str]] = [
    ("有凌晨写作的习惯", "habit"),
    ("习惯", "habit"),
    ("喜欢红茶", "pref.food"),
    ("喜欢喝", "pref.food"),
    ("饮食", "pref.food"),
    ("喜欢听", "pref.music"),
]


def _normalize_fact(fact) -> dict:
    if isinstance(fact, dict):
        return {
            "text": str(fact.get("text", "")),
            "tag": str(fact.get("tag", "misc")),
            "ts": float(fact.get("ts", 0)),
        }
    return {"text": str(fact), "tag": "misc", "ts": 0.0}


def _lookup_hand(text: str, table: dict[str, str | None]) -> tuple[str | None, bool]:
    """前缀查表，返回 (tag_or_None, found)。"""
    for prefix, tag in table.items():
        if text.startswith(prefix):
            return tag, True
    return None, False


def _classify_hint(text: str) -> str:
    for hint, tag in KEYWORD_HINTS:
        if hint in text:
            return tag
    return "stable"


def migrate_one(
    path: Path,
    hand_table: dict[str, str | None] | None,
    dry_run: bool,
) -> bool:
    data = json.loads(path.read_text("utf-8"))
    facts = data.get("important_facts") or []
    if not facts:
        print("  – No facts, skipping.")
        return False

    out: list[dict] = []
    seen: set[str] = set()
    changed = False

    for raw in facts:
        norm = _normalize_fact(raw)
        text = norm["text"].strip()
        if not text:
            continue

        if hand_table is not None:
            mapped_tag, found = _lookup_hand(text, hand_table)
            if found and mapped_tag is None:
                print(f"  DEL [{norm['tag']:15s}] {text[:70]}")
                changed = True
                continue
            elif found:
                new_tag = mapped_tag
            else:
                existing_tag = norm["tag"]
                new_tag = existing_tag if existing_tag not in ("misc", "") else "stable"
        else:
            existing_tag = norm["tag"]
            new_tag = existing_tag if existing_tag not in ("misc", "") else _classify_hint(text)

        if text in seen:
            print(f"  DUP [{norm['tag']:15s}] {text[:70]}")
            changed = True
            continue
        seen.add(text)

        old_tag = norm["tag"]
        if old_tag != new_tag or isinstance(raw, str):
            print(f"  TAG [{old_tag:15s}→{new_tag:15s}] {text[:60]}")
            changed = True
        else:
            print(f"  OK  [{new_tag:15s}] {text[:70]}")

        out.append({"text": text, "tag": new_tag, "ts": NOW_TS})

    if not changed and len(out) == len(facts):
        return False

    if dry_run:
        print(f"  [dry-run] {len(facts)} → {len(out)} facts")
        return True

    data["important_facts"] = out
    bak = path.with_suffix(".json.bak")
    shutil.copy(path, bak)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    print(f"  Wrote {len(out)} facts (was {len(facts)}). Backup: {bak.name}")
    return True


def main(dry_run: bool = False) -> None:
    if not DATA_ROOT.exists():
        print(f"ERROR: {DATA_ROOT} not found. Run from repo root.")
        sys.exit(1)

    targets: list[tuple[Path, str, str]] = []
    for char_dir in sorted(DATA_ROOT.iterdir()):
        if not char_dir.is_dir():
            continue
        for uid_dir in sorted(char_dir.iterdir()):
            if not uid_dir.is_dir():
                continue
            profile = uid_dir / "profile.json"
            if profile.exists():
                targets.append((profile, uid_dir.name, char_dir.name))

    if not targets:
        print("No profile.json files found.")
        return

    primary_uid = _primary_uid()

    for path, uid, char_id in targets:
        label = f"{char_id}/{uid}"
        print(f"\n{'[DRY-RUN] ' if dry_run else ''}=== {label} ===")
        hand = HAND_PRIMARY_USER_YEXUAN if (primary_uid and uid == primary_uid and char_id == "yexuan") else None
        try:
            changed = migrate_one(path, hand, dry_run)
            if not changed:
                print(f"  – No changes needed.")
        except Exception as e:
            print(f"  ERROR: {e}")

    if dry_run:
        print("\n[Dry-run complete. No files were modified.]")
    else:
        print("\nMigration complete. .bak files alongside each modified profile.json.")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
