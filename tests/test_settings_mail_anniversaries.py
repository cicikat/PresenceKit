"""tests/test_settings_mail_anniversaries.py — 配置中心「可选」层补遗：
邮件通道完整表单 + 自定义纪念日 CRUD（用户反馈，birthday/mail 需要面板可编辑）
"""
import asyncio

import pytest
import yaml
from fastapi import HTTPException

from admin.routers import settings_misc as mod


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _patch(monkeypatch, path):
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)


# ── /settings/mail ───────────────────────────────────────────────────────────

def test_mail_placeholder_is_not_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, (
        "mail:\n  enabled: false\n  smtp_host: smtp.gmail.com\n"
        "  smtp_user: YOUR-接收的邮箱\n  smtp_password: YOUR-邮箱通行密码\n  to_addr: YOUR-邮箱\n"
    ))
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_mail_settings(auth=None))
    assert result["configured"] is False
    assert result["smtp_password_set"] is False


def test_mail_write_and_read_back_masked(tmp_path, monkeypatch):
    path = _write(tmp_path, "mail:\n  enabled: false\n  smtp_host: smtp.gmail.com\n")
    _patch(monkeypatch, path)

    result = asyncio.run(mod.update_mail_settings(
        mod.MailSettingsUpdate(
            enabled=True, smtp_user="me@gmail.com", smtp_password="realpassword123",
            to_addr="friend@example.com",
        ),
        auth=None,
    ))
    assert result["enabled"] is True
    assert result["configured"] is True
    assert result["smtp_password_set"] is True
    assert "realpassword123" not in result["smtp_password_masked"]

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["mail"]["smtp_password"] == "realpassword123"
    assert cfg["mail"]["smtp_host"] == "smtp.gmail.com"  # 未传入，不清空


def test_mail_update_rejects_empty_body(tmp_path, monkeypatch):
    path = _write(tmp_path, "mail:\n  enabled: false\n")
    _patch(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_mail_settings(mod.MailSettingsUpdate(), auth=None))
    assert exc.value.status_code == 422


def test_mail_update_rejects_bad_port(tmp_path, monkeypatch):
    path = _write(tmp_path, "mail:\n  enabled: false\n")
    _patch(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_mail_settings(mod.MailSettingsUpdate(smtp_port=70000), auth=None))
    assert exc.value.status_code == 422


# ── /settings/anniversaries ──────────────────────────────────────────────────

def test_anniversaries_empty_by_default(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_anniversaries(auth=None))
    assert result["anniversaries"] == []


def test_anniversaries_round_trip(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)

    body = mod.AnniversariesUpdate(anniversaries=[
        mod.AnniversaryItem(key="first_date", month=6, day=14, year_start=2024, prompt_years="在一起{years}年了"),
        mod.AnniversaryItem(key="anniv2", month=12, day=25),
    ])
    result = asyncio.run(mod.update_anniversaries(body, auth=None))
    assert len(result["anniversaries"]) == 2
    assert result["anniversaries"][0]["year_start"] == 2024
    assert "year_start" not in result["anniversaries"][1]

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["anniversaries"][0]["key"] == "first_date"

    readback = asyncio.run(mod.get_anniversaries(auth=None))
    assert readback["anniversaries"] == result["anniversaries"]


def test_anniversaries_rejects_impossible_date(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    body = mod.AnniversariesUpdate(anniversaries=[mod.AnniversaryItem(key="x", month=2, day=30)])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_anniversaries(body, auth=None))
    assert exc.value.status_code == 422


def test_anniversaries_rejects_empty_key(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    body = mod.AnniversariesUpdate(anniversaries=[mod.AnniversaryItem(key="  ", month=1, day=1)])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_anniversaries(body, auth=None))
    assert exc.value.status_code == 422


# ── /settings/diary ──────────────────────────────────────────────────────────

def test_diary_empty_by_default(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_diary_settings(auth=None))
    assert result == {"obsidian_path": "", "configured": False}


def test_diary_write_and_read_back(tmp_path, monkeypatch):
    path = _write(tmp_path, "diary:\n  characters:\n  - 叶瑄\n")
    _patch(monkeypatch, path)

    result = asyncio.run(mod.update_diary_settings(
        mod.DiarySettingsUpdate(obsidian_path=r"E:\obsidian_note\Calendar"), auth=None,
    ))
    assert result["obsidian_path"] == r"E:\obsidian_note\Calendar"
    assert result["configured"] is True

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["diary"]["obsidian_path"] == r"E:\obsidian_note\Calendar"
    assert cfg["diary"]["characters"] == ["叶瑄"]  # 未编辑的字段原样保留

    readback = asyncio.run(mod.get_diary_settings(auth=None))
    assert readback == {"obsidian_path": r"E:\obsidian_note\Calendar", "configured": True}


def test_diary_rejects_empty_path(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_diary_settings(mod.DiarySettingsUpdate(obsidian_path="   "), auth=None))
    assert exc.value.status_code == 422


# ── /settings/coplay-games ───────────────────────────────────────────────────

def test_coplay_games_empty_by_default(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_coplay_games(auth=None))
    assert result["game_whitelist"] == []


def test_coplay_games_round_trip(tmp_path, monkeypatch):
    path = _write(tmp_path, "coplay:\n  enabled: true\n  poll_interval: 10\n")
    _patch(monkeypatch, path)

    body = mod.CoplayGamesUpdate(game_whitelist=[
        mod.GameWhitelistEntry(name="示例游戏", process_name="game.exe", save_dir=r"C:\Saves\示例游戏"),
        mod.GameWhitelistEntry(name="无存档游戏", process_name="noop"),
    ])
    result = asyncio.run(mod.update_coplay_games(body, auth=None))
    assert len(result["game_whitelist"]) == 2
    assert result["game_whitelist"][0]["save_dir"] == r"C:\Saves\示例游戏"
    assert "save_dir" not in result["game_whitelist"][1]

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["coplay"]["game_whitelist"][0]["process_name"] == "game.exe"
    assert cfg["coplay"]["enabled"] is True  # 未编辑的字段原样保留
    assert cfg["coplay"]["poll_interval"] == 10

    readback = asyncio.run(mod.get_coplay_games(auth=None))
    assert readback["game_whitelist"] == result["game_whitelist"]


def test_coplay_games_rejects_empty_fields(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    body = mod.CoplayGamesUpdate(game_whitelist=[mod.GameWhitelistEntry(name="x", process_name="  ")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_coplay_games(body, auth=None))
    assert exc.value.status_code == 422


def test_coplay_games_rejects_duplicate_process_name(tmp_path, monkeypatch):
    path = _write(tmp_path, "other: {}\n")
    _patch(monkeypatch, path)
    body = mod.CoplayGamesUpdate(game_whitelist=[
        mod.GameWhitelistEntry(name="A", process_name="game.exe"),
        mod.GameWhitelistEntry(name="B", process_name="GAME"),  # 同一进程名，大小写/.exe 不敏感
    ])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_coplay_games(body, auth=None))
    assert exc.value.status_code == 422
