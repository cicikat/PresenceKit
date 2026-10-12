from admin_static_assets import assert_versioned_admin_asset
from pathlib import Path


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "admin" / "static"


def test_user_data_page_is_grouped_and_keeps_diagnostic_table_secondary():
    page = (STATIC / "pages" / "user-data.html").read_text(encoding="utf-8")
    source = (STATIC / "js" / "user-data.js").read_text(encoding="utf-8")

    assert 'id="user-data-content"' in page
    for group in ("stickers", "voice", "models"):
        assert f"data-asset-group=\"${{escapeHtml(group)}}\"" in source
    for category in ("sticker", "sticker_pack", "reference_audio", "gpt_model", "sovits_model", "live2d", "model3d"):
        assert category in source
    assert "renderStickerGroups" in source
    assert "renderUploadFields" in source
    assert '<details class="card user-data-advanced">' in source
    assert "user-data-upload-extra" not in source
    assert "asset.bindings" in source
    assert "status.current_role_bound" in source
    assert "status.backend_only" in source
    assert "status.read_only" in source


def test_user_data_upload_is_driven_by_backend_category_contract():
    source = (STATIC / "js" / "user-data.js").read_text(encoding="utf-8")
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")

    assert "meta.upload_fields.map" in source
    assert "meta.accept" in source
    assert "meta.extensions" in source
    assert "meta.max_bytes" in source
    assert "form.append(field, values[field])" in source
    assert "canonical user data" in source
    assert "legacy and bundled sources stay read-only" in source
    assert "@media (max-width: 767px)" in css
    assert ".user-data-category-grid" in css
    assert ".user-data-asset-meta" in css
    assert_versioned_admin_asset('js/user-data.js', index)


def test_user_data_i18n_covers_sources_status_scope_and_upload_states():
    runtime = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for key in (
        "user_data.group.stickers", "user_data.group.voice", "user_data.group.models",
        "user_data.source.user", "user_data.source.legacy", "user_data.source.bundled",
        "user_data.status.current_role_bound", "user_data.status.desktop_usable",
        "user_data.status.backend_only", "user_data.status.read_only",
        "user_data.scope.character", "user_data.scope.pack", "user_data.scope.emotion",
        "user_data.upload.title", "user_data.upload.failed", "user_data.advanced.title",
    ):
        assert runtime.count(f"'{key}'") == 2, key
