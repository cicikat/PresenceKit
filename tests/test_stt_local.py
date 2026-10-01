import threading

import pytest

from core import stt_local


@pytest.fixture(autouse=True)
def _clean():
    stt_local.reset_for_tests()
    yield
    stt_local.reset_for_tests()


class _Model:
    def __init__(self, name):
        self.name = name

    def transcribe(self, *_a, **_k):
        return iter(()), None


def _patch(monkeypatch, *, cuda_devices=1, cuda_error=None, loads=None):
    """Fake the heavy parts: load/smoke and the CUDA probe."""
    loads = loads if loads is not None else []

    def load(size, device, compute):
        loads.append((size, device, compute))
        if device == "cuda" and cuda_error:
            raise RuntimeError(cuda_error)
        return _Model(f"{size}/{device}/{compute}")

    monkeypatch.setattr(stt_local, "_load", load)
    monkeypatch.setattr(stt_local, "_smoke", lambda model: None)
    monkeypatch.setattr(stt_local, "_cuda_device_count", lambda: cuda_devices)
    return loads


def test_defaults_suit_unknown_cpu_hardware():
    cfg = stt_local.settings({})
    # The faster-whisper group is exactly what it was before the engine switch existed, and the default
    # engine is still faster_whisper, so existing installs behave identically.
    assert {key: cfg[key] for key in ("model_size", "device", "compute_type", "beam_size", "timeout_seconds")} == {
        "model_size": "small", "device": "auto", "compute_type": "int8", "beam_size": 5, "timeout_seconds": 20.0}
    assert cfg["engine"] == "faster_whisper"
    assert set(cfg) == {"engine", "model_size", "device", "compute_type", "beam_size", "timeout_seconds", "sherpa_onnx"}


def test_validation_rejects_bad_fields_and_cpu_with_gpu_only_precision():
    for bad in ({"model_size": "huge"}, {"device": "tpu"}, {"beam_size": 0},
                {"timeout_seconds": 1}, {"device": "cpu", "compute_type": "float16"}):
        with pytest.raises(ValueError):
            stt_local.validate(bad)
    assert stt_local.validate({"device": "cuda", "compute_type": "float16"})["compute_type"] == "float16"


def test_hand_edited_bad_block_degrades_to_defaults_at_runtime():
    assert stt_local.settings({"stt_local": {"model_size": "huge"}}) == stt_local.DEFAULTS


def test_auto_without_cuda_uses_cpu_and_records_why(monkeypatch):
    _patch(monkeypatch, cuda_devices=0)
    backend = stt_local.get_backend(stt_local.settings({}))
    assert backend["device"] == "cpu"
    assert backend["fallback"]["from"] == "cuda"
    assert "CUDA" in backend["fallback"]["reason"]


def test_auto_falls_back_to_cpu_when_cuda_runtime_library_is_missing(monkeypatch):
    loads = _patch(monkeypatch, cuda_error="Library cublas64_12.dll is not found or cannot be loaded")
    backend = stt_local.get_backend(stt_local.settings({"stt_local": {"compute_type": "float16"}}))
    assert backend["device"] == "cpu" and backend["compute_type"] == "int8"
    assert "cublas64_12" in backend["fallback"]["reason"]
    assert "cpu" in backend["fallback"]["reason"]  # tells the person the next step
    assert [load[1] for load in loads] == ["cuda", "cpu"]
    assert stt_local.snapshot({})["effective"]["fallback"]["from"] == "cuda"


def test_auto_adopts_cuda_only_after_a_real_inference(monkeypatch):
    _patch(monkeypatch)
    backend = stt_local.get_backend(stt_local.settings({}))
    assert backend["device"] == "cuda" and backend["fallback"] is None

    stt_local.reset_for_tests()
    monkeypatch.setattr(stt_local, "_smoke",
                        lambda model: (_ for _ in ()).throw(RuntimeError("cudnn64_9.dll missing")))
    assert stt_local.get_backend(stt_local.settings({}))["device"] == "cpu"


def test_explicit_cuda_unavailable_is_an_error_not_a_silent_downgrade(monkeypatch):
    _patch(monkeypatch, cuda_error="Library cublas64_12.dll is not found")
    with pytest.raises(stt_local.SttLocalError, match="cublas64_12"):
        stt_local.get_backend(stt_local.settings({"stt_local": {"device": "cuda"}}))
    _patch(monkeypatch, cuda_devices=0)
    stt_local.reset_for_tests()
    with pytest.raises(stt_local.SttLocalError, match="CUDA"):
        stt_local.get_backend(stt_local.settings({"stt_local": {"device": "cuda"}}))


def test_explicit_cuda_failure_is_surfaced_as_503_by_the_endpoint(monkeypatch):
    from admin.routers import transcribe
    _patch(monkeypatch, cuda_error="Library cublas64_12.dll is not found")
    monkeypatch.setattr(stt_local, "settings",
                        lambda config=None: stt_local.validate({"device": "cuda"}))
    with pytest.raises(RuntimeError, match="cublas64_12"):  # handler maps RuntimeError -> 503
        transcribe._transcribe_sync("fixture.wav")


def test_config_change_rebuilds_and_same_config_reuses(monkeypatch):
    loads = _patch(monkeypatch, cuda_devices=0)
    small = stt_local.settings({})
    first = stt_local.get_backend(small)
    assert stt_local.get_backend(small) is first
    assert len(loads) == 1
    base = stt_local.settings({"stt_local": {"model_size": "base"}})
    second = stt_local.get_backend(base)
    assert second is not first and second["model_size"] == "base"
    assert len(loads) == 2
    assert stt_local.snapshot({"stt_local": {"model_size": "base"}})["matches_config"] is True


def test_failed_reload_keeps_the_previous_working_instance(monkeypatch):
    _patch(monkeypatch, cuda_devices=0)
    good = stt_local.get_backend(stt_local.settings({}))
    _patch(monkeypatch, cuda_error="Library cublas64_12.dll is not found")
    result = stt_local.reload(stt_local.validate({"device": "cuda"}))
    assert result["ok"] is False and "cublas64_12" in result["error"]
    assert result["effective"]["device"] == "cpu"
    assert stt_local._active is good
    assert stt_local.snapshot({})["last_change"]["ok"] is False


def test_concurrent_requests_during_swap_never_see_a_half_built_model(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_load(size, device, compute):
        started.set()
        release.wait(timeout=5)
        return _Model(f"{size}/{device}/{compute}")

    monkeypatch.setattr(stt_local, "_load", slow_load)
    monkeypatch.setattr(stt_local, "_smoke", lambda model: None)
    monkeypatch.setattr(stt_local, "_cuda_device_count", lambda: 0)
    results = []
    cfg = stt_local.settings({})
    threads = [threading.Thread(target=lambda: results.append(stt_local.get_backend(cfg))) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=5)
    assert stt_local._active is None  # nothing published before the build finishes
    release.set()
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 3 and all(item is results[0] for item in results)


def test_failure_is_not_retried_on_every_request(monkeypatch):
    loads = _patch(monkeypatch, cuda_error="Library cublas64_12.dll is not found")
    cfg = stt_local.settings({"stt_local": {"device": "cuda"}})
    for _ in range(3):
        with pytest.raises(stt_local.SttLocalError):
            stt_local.get_backend(cfg)
    assert len(loads) == 1


def test_warmup_builds_backend_when_local_engine_is_selected(monkeypatch):
    loads = _patch(monkeypatch, cuda_devices=0)
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": False, "presets": {}, "routes": {}})
    stt_local.warmup()
    assert loads  # a model was actually built
    assert stt_local.get_backend()["key"] == stt_local._key(stt_local.settings())


def test_warmup_skips_when_remote_stt_is_enabled(monkeypatch):
    loads = _patch(monkeypatch, cuda_devices=0)
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": True, "presets": {}, "routes": {}})
    stt_local.warmup()
    assert not loads
    assert stt_local._active is None


def test_warmup_skips_when_no_local_engine_is_installed(monkeypatch):
    loads = _patch(monkeypatch, cuda_devices=0)
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": False, "presets": {}, "routes": {}})
    monkeypatch.setattr(stt_local.importlib.util, "find_spec", lambda _name: None)
    stt_local.warmup()
    assert not loads


def test_warmup_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": False, "presets": {}, "routes": {}})
    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(stt_local, "get_backend", boom)
    stt_local.warmup()  # must not raise


@pytest.mark.asyncio
async def test_warmup_async_runs_off_the_event_loop_and_never_raises(monkeypatch):
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": True, "presets": {}, "routes": {}})
    await stt_local.warmup_async()  # should return promptly, no exception


def test_probe_hardware_explains_next_step_without_loading_a_model(monkeypatch):
    import types, sys
    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    info = stt_local.probe_hardware()
    assert info["devices"] == ["cpu"] and info["hint"]
    assert "cuBLAS" in stt_local.diagnose("Library cublas64_12.dll is not found")


def test_local_transcription_is_logged_with_model_and_device(monkeypatch):
    from admin.routers import transcribe
    calls = []
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: calls.append(kw))
    monkeypatch.setattr("core.stt_local.get_backend", lambda cfg=None: {
        "model": type("M", (), {"transcribe": lambda self, *a, **k: ([], None)})(),
        "backend": "faster_whisper", "model_size": "small", "device": "cpu",
        "compute_type": "int8", "fallback": {"from": "cuda", "reason": "x"}})
    transcribe._transcribe_sync("fixture.wav")
    assert calls[0]["caller"] == "stt" and calls[0]["model"] == "small/cpu/int8"
    assert calls[0]["output_hint"] == "fallback:cuda" and calls[0]["ok"] is True
