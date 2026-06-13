"""
角色显示名权威来源。

规则：
- prompt/tool/scheduler 层按 scope 调用 get_char_name(char_id)，不读 config。
- 未持有显式 scope 的兼容调用可继续使用 get_active_char_name()。
- config.character.name 只用于选择 active_character（由 data_paths / pipeline 读取）。
- pipeline 未注册时返回受控占位符，不静默回退到任何硬编码角色名。
"""


def get_char_name(char_id: str | None = None) -> str:
    """返回指定角色或当前活跃角色的 character card name。

    char_id 显式传入时通过 character asset registry 解析，未知角色直接抛错，
    不得回退到当前活跃角色。char_id 未传时保持旧行为。

    pipeline 未注册（测试隔离、启动前）时返回 "(角色未加载)" 而非任何私有角色名。
    调用方若需要 fail-loud，自行检查返回值是否为该占位符。
    """
    if char_id is not None:
        from core.character_loader import load

        return load(char_id).name

    from core import pipeline_registry
    pl = pipeline_registry.get()
    if pl is not None and pl.character is not None:
        return pl.character.name
    return "(角色未加载)"


def get_active_char_name() -> str:
    """兼容别名：返回当前活跃角色显示名。"""
    return get_char_name()
