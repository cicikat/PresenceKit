#!/usr/bin/env python3
"""
scripts/setup_auth.py — 首次鉴权配置 CLI（DX 主路径，Brief 22 / SEC-AUTH-2 P4）

直接 import 本仓的 admin.token_registry 操作 data/runtime/auth/tokens.yaml + config.yaml，
不走 HTTP，不需要已运行的后端。幂等：可反复执行。

用法：
    python scripts/setup_auth.py               # 首次配置：生成 secret_key（如为空/占位）+ 五个标准 token
    python scripts/setup_auth.py --rotate-all   # 已存在的标准 token 一并轮换（旧值立即失效）

详见 docs/token-rotation.md。
"""
from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

CONFIG_PATH = REPO_ROOT / "config.yaml"
SECRETS_LOCAL_PATH = REPO_ROOT / "secrets.local.yaml"

# label -> (profile, 密码本里的"配置位置"提示，见 docs/token-rotation.md)
STANDARD_TOKENS: list[tuple[str, str, str]] = [
    ("desktop-main",   "desktop", "PresenceKit-desktop/config/client.local.json → adminToken"),
    ("mobile-main",    "mobile",  "手机 app 系统设置 → Token 弹窗"),
    ("watch-main",     "watch",  "Watch 端配置"),
    ("esp32-device",   "device", "固件配置（firmware/，烧录前写入）"),
    ("admin-panel",    "panel",  "浏览器面板登录框（localStorage qq_admin_key）"),
]


def _ensure_admin_secret() -> tuple[str, bool]:
    """确保 config.yaml 的 admin.secret_key 非空/非占位；返回 (值, 是否本次新生成)。"""
    import yaml

    text = CONFIG_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    current = str((data.get("admin") or {}).get("secret_key", "")).strip()

    from admin.token_registry import PLACEHOLDER_ADMIN_SECRET

    if current and current != PLACEHOLDER_ADMIN_SECRET:
        return current, False

    new_secret = secrets.token_urlsafe(32)
    new_text, count = re.subn(
        r'(?m)^(\s*secret_key:\s*).*$',
        lambda m: f'{m.group(1)}"{new_secret}"',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError(
            "未能在 config.yaml 中定位 admin.secret_key 行，请手动设置该字段后重跑本脚本"
        )
    CONFIG_PATH.write_text(new_text, encoding="utf-8")
    return new_secret, True


def _ensure_tokens(rotate_all: bool) -> dict[str, str]:
    """五个当前标准 label 逐个检查；返回本次拿到明文的 {label: token}（仅新建/轮换的）。"""
    from admin import token_registry

    plaintexts: dict[str, str] = {}
    existing = {r.label for r in token_registry.list_records()}
    for label, profile, _location in STANDARD_TOKENS:
        if label not in existing:
            plaintexts[label] = token_registry.create_token(label, scopes=[f"profile:{profile}"])
            print(f"  + 已创建 {label}（profile={profile}）")
        elif rotate_all:
            plaintexts[label] = token_registry.rotate_token(label)
            print(f"  ~ 已轮换 {label}（旧值立即失效）")
        else:
            print(f"  = {label} 已存在，跳过（--rotate-all 可强制轮换）")
    return plaintexts


def _merge_secrets_local(new_tokens: dict[str, str], admin_secret: str) -> None:
    """把本次拿到明文的 token + break-glass secret 写入 secrets.local.yaml；
    按 label 合并，已存在的密码本条目/用户自加内容不被覆盖。"""
    import yaml

    location_by_label = {label: loc for label, _profile, loc in STANDARD_TOKENS}

    doc: dict = {}
    if SECRETS_LOCAL_PATH.exists():
        doc = yaml.safe_load(SECRETS_LOCAL_PATH.read_text(encoding="utf-8")) or {}
    doc.setdefault("tokens", {})

    doc["break_glass_secret"] = admin_secret
    for label, token in new_tokens.items():
        doc["tokens"][label] = {
            "token": token,
            "配置位置": location_by_label.get(label, "待填写"),
        }

    header = (
        "# Emerald-presence 本地密码本 —— 明文凭据，永远不要提交 git。\n"
        "# 轮换方法与完整说明: docs/token-rotation.md\n"
    )
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_flow_style=False)
    SECRETS_LOCAL_PATH.write_text(header + body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rotate-all", action="store_true", help="已存在的标准 token 一并轮换（旧值立即失效）")
    args = parser.parse_args()

    print("正在初始化鉴权配置…")
    admin_secret, is_new = _ensure_admin_secret()
    if is_new:
        print("  + config.admin.secret_key 为空/占位，已生成新值")
    else:
        print("  = config.admin.secret_key 已设置，跳过")

    new_tokens = _ensure_tokens(args.rotate_all)
    _merge_secrets_local(new_tokens, admin_secret)

    print()
    print("✅ 鉴权初始化完成，凭据已写入 secrets.local.yaml（已 gitignore，勿提交）")
    print("🔑 管理面板: http://127.0.0.1:8080  →  登录 token 见密码本 admin-panel 条目")
    print("📋 各设备 token 的配置位置和轮换命令: docs/token-rotation.md")


if __name__ == "__main__":
    main()
