import asyncio

import pytest
from fastapi import HTTPException

from admin.routers import settings_local_runtime as router_mod
from admin_static_assets import read_admin_client_source, read_admin_page
from core import stt_local


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    stt_local.reset_for_tests()
    store = {"cfg": {}}
    monkeypatch.setattr(router_mod, "read_config_file", lambda _path: dict(store["cfg"]))
    monkeypatch.setattr(router_mod, "write_config_file", lambda _path, data: store.update(cfg=dict(data)))
    monkeypatch.setattr("core.config_loader.reload_config", lambda: store["cfg"])
    monkeypatch.setattr(router_mod, "get_config", lambda: store["cfg"])
    monkeypatch.setattr(stt_local, "_smoke", lambda model: None)
    monkeypatch.setattr(stt_local, "_cuda_device_count", lambda: 0)
    monkeypatch.setattr(stt_local, "_load", lambda size, device, compute: object())
    yield store
    stt_local.reset_for_tests()


def test_get_reports_configured_and_effective_state(_clean):
    view = asyncio.run(router_mod.get_local_runtime({}))
    assert view["stt"]["configured"]["model_size"] == "small"
    assert view["stt"]["effective"] is None
    assert "cuda" in view["options"]["devices"] and view["defaults"]["device"] == "auto"


def test_save_switches_first_then_persists_and_shows_fallback(_clean):
    body = router_mod.SttLocalUpdate(model_size="base", device="auto")
    result = asyncio.run(router_mod.update_local_stt(body, {}))
    assert result["saved"] is True
    assert _clean["cfg"]["stt_local"]["model_size"] == "base"
    effective = result["stt"]["effective"]
    assert effective["device"] == "cpu" and effective["fallback"]["from"] == "cuda"


def test_failed_switch_saves_nothing_and_keeps_the_working_instance(_clean, monkeypatch):
    asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(model_size="small"), {}))
    before = dict(_clean["cfg"]["stt_local"])
    good = stt_local._active

    def broken(size, device, compute):
        raise RuntimeError("Library cublas64_12.dll is not found")
    monkeypatch.setattr(stt_local, "_load", broken)
    monkeypatch.setattr(stt_local, "_cuda_device_count", lambda: 1)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(device="cuda"), {}))
    assert caught.value.status_code == 409
    assert caught.value.detail["saved"] is False
    assert "cublas64_12" in caught.value.detail["error"]
    assert caught.value.detail["kept"]["device"] == "cpu"
    assert _clean["cfg"]["stt_local"] == before
    assert stt_local._active is good


def test_invalid_values_are_rejected_before_any_switch(_clean):
    for bad in ({"model_size": "huge"}, {"beam_size": 99}, {"device": "cpu", "compute_type": "float16"}):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(**bad), {}))
        assert caught.value.status_code == 422
    assert stt_local._active is None and "stt_local" not in _clean["cfg"]


def test_hardware_probe_is_read_only(monkeypatch):
    monkeypatch.setattr(stt_local, "probe_hardware", lambda: {"devices": ["cpu"], "hint": "x"})
    assert asyncio.run(router_mod.get_local_runtime_hardware({})) == {"devices": ["cpu"], "hint": "x"}
    assert stt_local._active is None


def test_page_is_wired_with_costs_effective_state_and_version():
    source = read_admin_client_source()
    page = read_admin_page("local-model-runtime")
    for marker in ("local-runtime-model-size", "local-runtime-device", "local-runtime-compute-type",
                   "local-runtime-effective-body", "local-runtime-hardware-body"):
        assert marker in page
    assert "'local-model-runtime': loadLocalModelRuntime" in source
    assert "/settings/local-runtime/stt" in source and "/settings/local-runtime/hardware" in source
    assert "local_runtime.effective.fallback" in source
    assert "GB" in source  # each option states its cost, not just a bare dropdown
