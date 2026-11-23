"""The migrated selection reader must not silently select a config fallback."""
from types import SimpleNamespace

import pytest

from core.character_context import selected_character_id_or_none


@pytest.mark.parametrize("contents,expected", [
    (None, None), (b"not-json", None), (b"{}", None),
    (b'{"active_character":"  "}', None),
    (b'{"active_character":17}', None),
    (b'{"active_character":"  selected-char  "}', "selected-char"),
])
def test_selected_character_reads_only_selected_assets(sandbox, monkeypatch, contents, expected):
    monkeypatch.setattr("core.character_context.get_config", lambda: {"character": {"default": "fallback-char"}})
    path = sandbox.active_prompt_assets()
    path.parent.mkdir(parents=True, exist_ok=True)
    if contents is not None:
        path.write_bytes(contents)
    else:
        path.unlink(missing_ok=True)
        # Model an unavailable file after path resolution. DataPaths itself
        # legitimately seeds a selection on first access, before this reader.
        monkeypatch.setattr("core.character_context._get_paths", lambda: SimpleNamespace(active_prompt_assets=lambda: path))
    assert selected_character_id_or_none() == expected
