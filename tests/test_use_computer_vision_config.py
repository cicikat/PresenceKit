"""
tests/test_use_computer_vision_config.py

get_use_computer_vision_config()：桌面自动化自己的第二个视觉槽位
（dedicated > 通用 vision 回落）；手机自动化已并入 screen 路由，不再与它同构。
不按角色路由——图像识别是通用能力。
"""

from unittest.mock import patch

from core.perception.vlm_client import get_use_computer_vision_config


def test_falls_back_to_general_vision_when_no_dedicated_block():
    cfg = {"vision": {"base_url": "https://vision.example", "model": "glm-4v-flash", "api_key": "k"}}
    with patch("core.config_loader.get_config", return_value=cfg):
        resolved = get_use_computer_vision_config()
    assert resolved == {"base_url": "https://vision.example", "model": "glm-4v-flash", "api_key": "k"}


def test_dedicated_block_overrides_general_vision():
    cfg = {
        "vision": {"base_url": "https://vision.example", "model": "glm-4v-flash", "api_key": "k"},
        "use_computer_vision": {"model": "glm-4.6v"},
    }
    with patch("core.config_loader.get_config", return_value=cfg):
        resolved = get_use_computer_vision_config()
    assert resolved["model"] == "glm-4.6v"
    assert resolved["base_url"] == "https://vision.example"  # untouched field still inherited
    assert resolved["api_key"] == "k"


def test_dedicated_blank_fields_do_not_clobber_general_values():
    cfg = {
        "vision": {"base_url": "https://vision.example", "model": "glm-4v-flash"},
        "use_computer_vision": {"model": ""},  # explicit blank must not win over general
    }
    with patch("core.config_loader.get_config", return_value=cfg):
        resolved = get_use_computer_vision_config()
    assert resolved["model"] == "glm-4v-flash"


def test_missing_both_blocks_returns_empty_dict():
    with patch("core.config_loader.get_config", return_value={}):
        resolved = get_use_computer_vision_config()
    assert resolved == {}
