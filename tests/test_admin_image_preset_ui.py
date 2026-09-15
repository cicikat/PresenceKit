"""UI contract for named image connections and purpose routing."""
from admin_static_assets import read_admin_client_source, read_admin_page


def test_model_routing_exposes_named_image_preset_crud_and_purpose_selects():
    page = read_admin_page("model-routing")
    source = read_admin_client_source()

    for marker in (
        'data-action="openCreateImagePreset"',
        'id="vision-connections-body"',
        'id="image-routes-body"',
        'id="image-preset-name"',
        'id="image-preset-kind"',
        'data-action="saveImagePreset"',
        'data-action="saveImageRoutes"',
        'data-action="deleteImagePreset"',
        'id="phone-vision-enabled"',
        'id="image-recognition-mode"',
    ):
        assert marker in page or marker in source

    for path in (
        "'/image-presets'",
        "`/image-presets/presets/${encodeURIComponent(name)}`",
        "'/image-presets/routes'",
        "`/image-recognition/test/${encodeURIComponent(connection)}`",
        "function openCreateImagePreset()",
        "function saveImageRoutes()",
        "function deleteImagePreset(",
        "chat_upload",
        "life_diet",
        "life_cart",
        "life_bill",
        "phone_automation",
    ):
        assert path in source
