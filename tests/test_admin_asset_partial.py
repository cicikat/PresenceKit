import asyncio

from admin.routers.user_data import list_user_assets
from core import userdata_assets


def test_bad_character_scope_does_not_hide_global_stickers(sandbox):
    userdata_assets.store_upload(category="sticker", logical_id="wave", filename="wave.png",
                                content=b"png", emotion="happy")
    result = asyncio.run(list_user_assets(char_id="\u89d2\u8272\u663e\u793a\u540d", auth=None))
    assert result["partial"]
    assert any(row["logical_id"] == "wave" for row in result["assets"])
    assert {row["category"] for row in result["errors"]} == {"reference_audio", "gpt_model", "sovits_model"}
    assert all("path" not in row for row in result["errors"])


def test_missing_assets_are_empty_without_error(sandbox):
    result = asyncio.run(list_user_assets(char_id="role-clean", auth=None))
    assert result["assets"] == [] and result["errors"] == [] and not result["partial"]
