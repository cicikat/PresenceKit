"""
SEC-AUTH-2 scope model：13 个 scope + profile 预置组合。
详见 cc-tasks/21-鉴权分层-scoped-tokens.md §2。
"""

SCOPES: frozenset[str] = frozenset({
    "admin",
    "chat",
    "state.read",
    "memory.read",
    "sensor.write",
    "integration.write",
    "companion.write",
    "diary.sync",
    "life_records",
    "activity",
    "persona",
    "hardware",
    "ws.desktop",
    "ws.device",
})

PROFILES: dict[str, frozenset[str]] = {
    "desktop": frozenset({
        "chat", "state.read", "memory.read", "activity", "persona",
        "hardware", "sensor.write", "ws.desktop", "diary.sync",
    }),
    "mobile": frozenset({
        "chat", "state.read", "memory.read", "activity", "persona", "sensor.write",
        "life_records",
    }),
    "sensor": frozenset({"sensor.write"}),
    "watch": frozenset({"sensor.write"}),
    "integration": frozenset({"integration.write"}),
    "companion": frozenset({"companion.write"}),
    "owner-input": frozenset({"chat"}),
    "device": frozenset({"ws.device"}),
    "panel": frozenset({"admin"}),
}

_PROFILE_PREFIX = "profile:"


def expand_scopes(raw: list[str]) -> frozenset[str]:
    """把 token 记录里的原始 scopes 列表（`profile:xxx` 和/或显式 scope，可混用）展开成扁平 scope 集合。

    未知 profile / scope 名直接抛 ValueError，让加载方决定是跳过该条记录还是失败。
    """
    out: set[str] = set()
    for item in raw:
        if item.startswith(_PROFILE_PREFIX):
            name = item[len(_PROFILE_PREFIX):]
            if name not in PROFILES:
                raise ValueError(f"unknown profile: {name!r}")
            out |= PROFILES[name]
        else:
            if item not in SCOPES:
                raise ValueError(f"unknown scope: {item!r}")
            out.add(item)
    return frozenset(out)
