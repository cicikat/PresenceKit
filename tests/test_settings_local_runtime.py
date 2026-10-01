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


# ── 工单 H：引擎选择 / sherpa 分组 / 下载 ──────────────────────────────────────

def _fake_sherpa_build(monkeypatch):
    from core import stt_sherpa

    def build(group):
        return {"model": object(), "backend": "sherpa_onnx", "device": "cpu", "model_size": group["model"],
                "compute_type": "int8", "fallback": None, "hotwords": "none"}
    monkeypatch.setattr(stt_sherpa, "build", build)


def test_get_lists_engines_sherpa_models_and_whether_remote_stt_overrides(_clean):
    view = asyncio.run(router_mod.get_local_runtime({}))
    assert view["options"]["engines"] == ["faster_whisper", "sherpa_onnx"]
    assert view["stt"]["configured"]["engine"] == "faster_whisper"            # 默认引擎不变
    assert view["options"]["sherpa_models"] and "modified_beam_search" in view["options"]["sherpa_decoding_methods"]
    assert "installed" in view["sherpa"] and "models" in view["sherpa"]
    assert view["remote_stt_overrides_local"] is False
    _clean["cfg"] = {"stt_presets": {"enabled": False}}
    assert asyncio.run(router_mod.get_local_runtime({}))["remote_stt_overrides_local"] is True


def test_switching_to_sherpa_saves_the_group_and_reports_the_new_engine(_clean, monkeypatch):
    _fake_sherpa_build(monkeypatch)
    body = router_mod.SttLocalUpdate(engine="sherpa_onnx", sherpa_onnx={"num_threads": 4, "max_active_paths": 6})
    result = asyncio.run(router_mod.update_local_stt(body, {}))
    saved = _clean["cfg"]["stt_local"]
    assert result["saved"] and saved["engine"] == "sherpa_onnx"
    assert saved["sherpa_onnx"]["num_threads"] == 4 and saved["sherpa_onnx"]["max_active_paths"] == 6
    assert result["stt"]["effective"]["backend"] == "sherpa_onnx"


def test_switching_engines_keeps_the_other_groups_values(_clean, monkeypatch):
    _fake_sherpa_build(monkeypatch)
    asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(model_size="base", beam_size=2), {}))
    asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(
        engine="sherpa_onnx", sherpa_onnx={"hotwords_score": 3.0}), {}))
    saved = _clean["cfg"]["stt_local"]
    assert (saved["model_size"], saved["beam_size"]) == ("base", 2)             # Whisper 组没被清空
    asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(engine="faster_whisper"), {}))
    saved = _clean["cfg"]["stt_local"]
    assert saved["engine"] == "faster_whisper" and saved["sherpa_onnx"]["hotwords_score"] == 3.0   # sherpa 组也保留
    assert stt_local._active["backend"] == "faster_whisper"                       # 切回来真的换了实例


def test_selecting_sherpa_without_the_package_fails_loudly_saves_nothing_and_keeps_whisper(_clean, monkeypatch):
    import sys
    asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(model_size="small"), {}))
    before, good = dict(_clean["cfg"]["stt_local"]), stt_local._active
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(engine="sherpa_onnx"), {}))
    assert caught.value.status_code == 409 and caught.value.detail["saved"] is False
    assert "pip install sherpa-onnx" in caught.value.detail["error"]
    assert caught.value.detail["kept"]["backend"] == "faster_whisper"            # 没有悄悄换引擎
    assert _clean["cfg"]["stt_local"] == before and stt_local._active is good


def test_invalid_sherpa_values_are_rejected_before_any_switch(_clean):
    for bad in ({"num_threads": 0}, {"decoding_method": "beam"}, {"model": "nope"}, {"download_base": "ftp://x"}):
        with pytest.raises(HTTPException) as caught:
            asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(
                engine="sherpa_onnx", sherpa_onnx=bad), {}))
        assert caught.value.status_code == 422
    with pytest.raises(HTTPException):
        asyncio.run(router_mod.update_local_stt(router_mod.SttLocalUpdate(engine="vosk"), {}))
    assert stt_local._active is None and "stt_local" not in _clean["cfg"]


def test_download_endpoint_validates_the_model_and_is_single_flight(_clean, monkeypatch):
    from core import stt_sherpa
    started = []
    monkeypatch.setattr(stt_sherpa, "start_download", lambda model, base: started.append((model, base)) or len(started) == 1)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(model="nope"), {}))
    assert caught.value.status_code == 422 and started == []
    ok = asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(), {}))
    assert ok["started"] is True and started == [(stt_sherpa.DEFAULT_MODEL, "https://huggingface.co")]
    with pytest.raises(HTTPException) as busy:
        asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(), {}))
    assert busy.value.status_code == 409


def test_download_endpoint_prefers_the_typed_mirror_over_the_saved_one_and_validates_it(_clean, monkeypatch):
    from core import stt_sherpa
    _clean["cfg"] = {"stt_local": {"sherpa_onnx": {"download_base": "https://saved.example"}}}
    seen = []
    monkeypatch.setattr(stt_sherpa, "start_download", lambda model, base: seen.append(base) or True)
    asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(download_base="https://typed.example/"), {}))
    assert seen == ["https://typed.example"]
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(download_base="ftp://nope"), {}))
    assert caught.value.status_code == 422 and seen == ["https://typed.example"]


def test_download_endpoint_uses_the_configured_mirror(_clean, monkeypatch):
    from core import stt_sherpa
    _clean["cfg"] = {"stt_local": {"sherpa_onnx": {"download_base": "https://mirror.example/"}}}
    seen = []
    monkeypatch.setattr(stt_sherpa, "start_download", lambda model, base: seen.append(base) or True)
    asyncio.run(router_mod.download_sherpa_model(router_mod.SherpaDownload(), {}))
    assert seen == ["https://mirror.example"]


def test_new_endpoints_are_admin_scoped():
    import inspect
    source = inspect.getsource(router_mod)
    assert source.count('require_scopes("admin")') >= 4 and "stt/sherpa/download" in source
