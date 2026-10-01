"""工单 H：sherpa-onnx 引擎——配置/分组校验、引擎纳入 key、重复折叠、下载校验、fail-loud、适配层。

原生 sherpa-onnx 库没有在本环境运行过；这里用假的 ``sherpa_onnx`` 模块驱动真实适配代码，
只证明「我们怎样调用它」，不证明识别效果（见 docs/audio-perception.md 的未实测清单）。
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import io
import sys
import types
import wave

import pytest

from core import stt_local, stt_sherpa

# Bind everything that captures get_config at import time before any test patches it.
from admin.routers import settings_local_runtime as router_mod  # noqa: F401
from admin.routers import transcribe as transcribe_mod


# ── fixtures ───────────────────────────────────────────────────────────────────

FAKE_FILES = {"encoder": b"E" * 40, "decoder": b"D" * 10, "joiner": b"J" * 8,
              "tokens": b"<blk> 0\n", "bpe_vocab": b"a 1\n"}
FAKE_NAMES = {"encoder": "enc.int8.onnx", "decoder": "dec.onnx", "joiner": "join.int8.onnx",
              "tokens": "tokens.txt", "bpe_vocab": "bpe.vocab"}


def _fake_manifest():
    return {"fake-model": {
        "label": "fake", "repo": "owner/fake", "commit": "c0ffee", "modeling_unit": "cjkchar+bpe",
        "sample_rate": 16000, "feature_dim": 80,
        "files": {role: {"name": FAKE_NAMES[role], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for role, data in FAKE_FILES.items()},
    }}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    stt_local.reset_for_tests()
    stt_sherpa.reset_for_tests()
    monkeypatch.setattr(stt_sherpa, "MODELS", _fake_manifest())
    monkeypatch.setattr(stt_sherpa, "DEFAULT_MODEL", "fake-model")
    monkeypatch.setitem(stt_sherpa.DEFAULTS, "model", "fake-model")
    monkeypatch.setitem(stt_local.DEFAULTS, "sherpa_onnx", dict(stt_sherpa.DEFAULTS))
    yield
    stt_local.reset_for_tests()
    stt_sherpa.reset_for_tests()


@pytest.fixture
def assets(sandbox):
    """The fake model fully present on disk."""
    target = stt_sherpa.model_dir("fake-model")
    target.mkdir(parents=True, exist_ok=True)
    for role, data in FAKE_FILES.items():
        (target / FAKE_NAMES[role]).write_bytes(data)
    return target


class FakeStream:
    def __init__(self):
        self.waves, self.finished = [], False

    def accept_waveform(self, rate, samples):
        self.waves.append((rate, len(samples)))

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    """Scripted OnlineRecognizer: ``endpoints`` maps a decode step to the text that closes a segment."""
    instances: list["FakeRecognizer"] = []

    def __init__(self, kwargs, endpoints=None, tail="", steps=6, boom=False):
        self.kwargs, self.endpoints, self.tail, self.steps, self.boom = kwargs, endpoints or {}, tail, steps, boom
        self.step, self.resets, self.streams, self.pending = 0, 0, [], ""
        FakeRecognizer.instances.append(self)

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        return self.step < self.steps

    def decode_stream(self, stream):
        if self.boom:
            raise RuntimeError("ort failure")
        self.step += 1
        self.pending = self.endpoints.get(self.step, self.pending)

    def is_endpoint(self, stream):
        return self.step in self.endpoints

    def get_result(self, stream):
        return self.pending if self.step in self.endpoints else (self.tail if self.step >= self.steps else "")

    def reset(self, stream):
        self.resets += 1
        self.pending = ""


def install_fake_sherpa(monkeypatch, *, reject_hotwords=False, **recognizer_kwargs):
    module = types.ModuleType("sherpa_onnx")
    module.__spec__ = importlib.machinery.ModuleSpec("sherpa_onnx", None)
    calls = []

    class OnlineRecognizer:
        @classmethod
        def from_transducer(cls, **kwargs):
            calls.append(kwargs)
            if reject_hotwords and "hotwords_file" in kwargs:
                raise RuntimeError("hotwords rejected")
            return FakeRecognizer(kwargs, **recognizer_kwargs)

    module.OnlineRecognizer = OnlineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", module)
    FakeRecognizer.instances.clear()
    return calls


def sherpa_cfg(**overrides):
    return stt_local.validate({"engine": "sherpa_onnx", "sherpa_onnx": overrides})


# ── settings ───────────────────────────────────────────────────────────────────

def test_default_engine_is_faster_whisper_and_group_defaults_are_the_repeat_safe_ones():
    cfg = stt_local.settings({})
    assert cfg["engine"] == "faster_whisper"
    group = cfg["sherpa_onnx"]
    assert group["decoding_method"] == "modified_beam_search"        # 不用纯贪心
    assert group["repeat_collapse_min_run"] == 4 and group["endpoint_silence_seconds"] > 0


@pytest.mark.parametrize("bad", [
    {"model": "nope"}, {"decoding_method": "beam"}, {"num_threads": 0}, {"num_threads": True},
    {"max_active_paths": 99}, {"hotwords_score": -1}, {"endpoint_silence_seconds": 0.01},
    {"repeat_collapse_min_run": 2}, {"download_base": "ftp://x"}, {"download_base": "https://u:p@x"},
    {"download_base": "https://x/?q=1"},
])
def test_sherpa_group_is_strictly_validated_when_it_is_the_active_engine(bad):
    with pytest.raises(ValueError):
        stt_local.validate({"engine": "sherpa_onnx", "sherpa_onnx": bad})


def test_unknown_engine_is_rejected_and_hand_edited_garbage_degrades_to_defaults():
    with pytest.raises(ValueError):
        stt_local.validate({"engine": "vosk"})
    assert stt_local.settings({"stt_local": {"engine": "vosk"}})["engine"] == "faster_whisper"


def test_only_the_active_group_can_block_a_save_and_the_other_group_is_kept():
    # Active faster-whisper with a junk sherpa block: whisper still saves, sherpa resets to defaults.
    cfg = stt_local.validate({"engine": "faster_whisper", "model_size": "base", "sherpa_onnx": {"num_threads": 0}})
    assert cfg["model_size"] == "base" and cfg["sherpa_onnx"]["num_threads"] == stt_sherpa.DEFAULTS["num_threads"]
    # Active sherpa with junk whisper keys: sherpa saves, whisper falls back to defaults.
    cfg = stt_local.validate({"engine": "sherpa_onnx", "model_size": "huge", "sherpa_onnx": {"num_threads": 4}})
    assert cfg["sherpa_onnx"]["num_threads"] == 4 and cfg["model_size"] == "small"
    # Switching back and forth keeps both valid groups.
    both = {"model_size": "medium", "beam_size": 3, "sherpa_onnx": {"num_threads": 6, "hotwords_score": 2.5}}
    to_sherpa = stt_local.validate({**both, "engine": "sherpa_onnx"})
    back = stt_local.validate({**to_sherpa, "engine": "faster_whisper"})
    assert (back["model_size"], back["beam_size"]) == ("medium", 3)
    assert back["sherpa_onnx"]["num_threads"] == 6 and back["sherpa_onnx"]["hotwords_score"] == 2.5


# ── engine is part of the instance identity ────────────────────────────────────

def test_engine_is_part_of_the_key_and_switching_rebuilds_every_time(monkeypatch):
    builds = []

    def fake_whisper(cfg):
        builds.append("faster_whisper")
        return {"model": object(), "backend": "faster_whisper", "device": "cpu", "model_size": cfg["model_size"],
                "compute_type": "int8", "fallback": None}

    def fake_sherpa(group):
        builds.append("sherpa_onnx")
        return {"model": object(), "backend": "sherpa_onnx", "device": "cpu", "model_size": group["model"],
                "compute_type": "int8", "fallback": None}

    monkeypatch.setattr(stt_local, "_build", fake_whisper)
    monkeypatch.setattr(stt_sherpa, "build", fake_sherpa)
    whisper, sherpa = stt_local.settings({}), sherpa_cfg()
    assert stt_local._key(whisper)[0] == "faster_whisper" and stt_local._key(sherpa)[0] == "sherpa_onnx"
    assert stt_local._key(whisper) != stt_local._key(sherpa)
    first = stt_local.get_backend(whisper)
    assert stt_local.get_backend(whisper) is first and builds == ["faster_whisper"]
    assert stt_local.get_backend(sherpa)["backend"] == "sherpa_onnx"
    assert stt_local.get_backend(whisper)["backend"] == "faster_whisper"       # 切回来也真的换了
    assert stt_local.get_backend(sherpa)["backend"] == "sherpa_onnx"
    assert builds == ["faster_whisper", "sherpa_onnx", "faster_whisper", "sherpa_onnx"]


def test_sherpa_key_changes_with_decoder_settings_and_vocabulary(monkeypatch):
    base = stt_local._key(sherpa_cfg())
    assert stt_local._key(sherpa_cfg(max_active_paths=8)) != base
    assert stt_local._key(sherpa_cfg(decoding_method="greedy_search")) != base
    assert stt_local._key(sherpa_cfg(download_base="https://mirror.example")) == base      # 下载源不影响实例
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {
        "enabled": True, "entries": [{"heard": "mu xing", "canonical": "暮星"}]})
    assert stt_local._key(sherpa_cfg()) != base                                        # 词表变了要重建


# ── post-processing ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("好好看看", "好好看看"), ("对对对", "对对对"), ("我我我 想想 去", "我我我 想想 去"),     # 合法叠字/短重复不动
    ("哈哈哈哈哈哈", "哈哈"), ("的的的的的的的好", "的的好"), ("看看看看看", "看看"),         # 异常重复折叠
    ("1000000", "1000000"), ("……。。。。", "……。。。。"),                                # 数字/标点不动
    ("A A A A A B", "A A B"), ("HELLO HELLO HELLO", "HELLO HELLO HELLO"), ("", ""),
])
def test_collapse_repeats_folds_runaway_runs_but_never_legitimate_reduplication(text, want):
    assert stt_sherpa.collapse_repeats(text) == want


def test_collapse_threshold_is_configurable_and_bounded():
    assert stt_sherpa.collapse_repeats("对对对", 3) == "对对"
    assert stt_sherpa.collapse_repeats("好好好好", 5) == "好好好好"
    assert stt_sherpa.collapse_repeats("哈哈哈哈", 1) == "哈哈哈哈"          # min_run<2 视为关闭


def test_normalize_lowercases_long_english_but_keeps_short_acronyms():
    assert stt_sherpa.normalize_text("今天 HELLO WORLD 我用 AI 做 PRESENCE") == "今天 hello world 我用 AI 做 presence"


def test_segments_join_without_spaces_between_cjk_and_with_spaces_between_latin():
    assert stt_sherpa._smart_join(["你好", "世界"]) == "你好世界"
    assert stt_sherpa._smart_join(["HELLO", "WORLD"]) == "HELLO WORLD"
    assert stt_sherpa._smart_join(["你好", "", "OK"]) == "你好OK"


# ── recognition loop ───────────────────────────────────────────────────────────

def test_recognize_cuts_segments_at_endpoints_pads_the_tail_and_collapses_repeats():
    import numpy as np
    rec = FakeRecognizer({}, endpoints={2: "你好 PRESENCE", 4: "的的的的的的"}, tail="再见", steps=6)
    text = stt_sherpa.recognize(rec, np.zeros(16000, dtype="float32"))
    assert text == "你好 presence的的再见"
    assert rec.resets == 2                                        # 每个端点后都 reset
    stream = rec.streams[0]
    assert stream.finished and stream.waves[0] == (16000, 16000)
    assert stream.waves[1][1] == int(0.66 * 16000)                 # 尾部静音补齐，最后一个音节才不丢


def test_recognize_of_silence_is_empty_not_an_error():
    import numpy as np
    assert stt_sherpa.recognize(FakeRecognizer({}, steps=3), np.zeros(8000, dtype="float32")) == ""


def test_wav_without_faster_whisper_is_decoded_and_resampled(monkeypatch, tmp_path):
    import numpy as np
    monkeypatch.setitem(sys.modules, "faster_whisper.audio", None)         # 让 import 失败
    path = tmp_path / "clip.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2), handle.setsampwidth(2), handle.setframerate(8000)
        handle.writeframes((np.ones(8000 * 2, dtype="<i2") * 1000).tobytes())
    samples = stt_sherpa._decode_audio(str(path), 16000)
    assert abs(len(samples) - 16000) <= 1 and 0.02 < float(samples[100]) < 0.04
    (tmp_path / "x.webm").write_bytes(b"not wav")
    with pytest.raises(stt_local.SttLocalError, match="faster-whisper"):
        stt_sherpa._decode_audio(str(tmp_path / "x.webm"), 16000)


# ── build: fail loud, never fall back ──────────────────────────────────────────

def test_missing_package_is_an_actionable_error(monkeypatch, assets):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    with pytest.raises(stt_local.SttLocalError, match="pip install sherpa-onnx"):
        stt_sherpa.build(stt_sherpa.validate({}))


def test_missing_or_corrupt_model_files_name_the_problem_and_the_next_step(monkeypatch, sandbox):
    install_fake_sherpa(monkeypatch)
    with pytest.raises(stt_local.SttLocalError) as caught:
        stt_sherpa.build(stt_sherpa.validate({}))
    assert "缺少 enc.int8.onnx" in str(caught.value) and "下载模型" in str(caught.value)
    assert "Whisper" in str(caught.value) and "不会自动回落" in str(caught.value)


def test_same_size_but_wrong_content_is_rejected_by_sha256(monkeypatch, assets):
    install_fake_sherpa(monkeypatch)
    (assets / FAKE_NAMES["encoder"]).write_bytes(b"X" * len(FAKE_FILES["encoder"]))   # 大小对、内容不对
    with pytest.raises(stt_local.SttLocalError, match="enc.int8.onnx 校验不通过"):
        stt_sherpa.build(stt_sherpa.validate({}))


def test_explicit_sherpa_failure_never_falls_back_to_whisper(monkeypatch, sandbox):
    whisper_built = []
    monkeypatch.setattr(stt_local, "_build", lambda cfg: whisper_built.append(cfg))
    monkeypatch.setattr(stt_local, "_build_legacy", lambda cfg: whisper_built.append(cfg))
    install_fake_sherpa(monkeypatch)
    with pytest.raises(stt_local.SttLocalError):
        stt_local.get_backend(sherpa_cfg())
    assert whisper_built == []


def test_build_wires_endpointing_beam_search_and_returns_a_metrics_compatible_backend(monkeypatch, assets):
    calls = install_fake_sherpa(monkeypatch)
    backend = stt_sherpa.build(stt_sherpa.validate({"num_threads": 3, "max_active_paths": 6, "endpoint_silence_seconds": 1.0}))
    kwargs = calls[0]
    assert kwargs["enable_endpoint_detection"] is True                       # 不复刻输入法的关端点
    assert kwargs["decoding_method"] == "modified_beam_search" and kwargs["max_active_paths"] == 6
    assert kwargs["rule2_min_trailing_silence"] == 1.0 and kwargs["rule1_min_trailing_silence"] >= 2.4
    assert kwargs["num_threads"] == 3 and kwargs["provider"] == "cpu" and kwargs["sample_rate"] == 16000
    assert kwargs["encoder"].endswith(FAKE_NAMES["encoder"])
    # transcribe.py 的埋点直接读这几个字段，缺一个就 KeyError
    for field in ("backend", "model_size", "device", "compute_type", "fallback", "model"):
        assert field in backend
    assert backend["backend"] == "sherpa_onnx" and backend["fallback"] is None


def test_smoke_failure_is_an_error_and_not_an_instance(monkeypatch, assets):
    install_fake_sherpa(monkeypatch, boom=True)
    with pytest.raises(stt_local.SttLocalError, match="自检失败"):
        stt_local.get_backend(sherpa_cfg())
    assert stt_local._active is None


def test_vocabulary_is_passed_as_hotwords_with_the_bpe_vocab(monkeypatch, assets):
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {"enabled": True, "entries": [
        {"heard": "mu xing", "canonical": "暮星"}, {"heard": "e m", "canonical": "Emerald"},
        {"heard": "dup", "canonical": "暮星"}]})
    calls = install_fake_sherpa(monkeypatch)
    backend = stt_sherpa.build(stt_sherpa.validate({"hotwords_score": 2.0}))
    kwargs = calls[0]
    assert kwargs["modeling_unit"] == "cjkchar+bpe" and kwargs["hotwords_score"] == 2.0
    assert kwargs["bpe_vocab"].endswith(FAKE_NAMES["bpe_vocab"])
    from pathlib import Path
    assert Path(kwargs["hotwords_file"]).read_text(encoding="utf-8").splitlines() == ["暮星", "EMERALD"]   # 去重 + ASCII 大写
    assert backend["hotwords"] == "active"


def test_no_vocabulary_means_no_hotword_arguments(monkeypatch, assets):
    calls = install_fake_sherpa(monkeypatch)
    assert stt_sherpa.build(stt_sherpa.validate({}))["hotwords"] == "none"
    assert not {"hotwords_file", "hotwords_score", "modeling_unit", "bpe_vocab"} & set(calls[0])


def test_greedy_search_disables_hotwords_explicitly(monkeypatch, assets):
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {
        "enabled": True, "entries": [{"heard": "mu xing", "canonical": "暮星"}]})
    calls = install_fake_sherpa(monkeypatch)
    backend = stt_sherpa.build(stt_sherpa.validate({"decoding_method": "greedy_search"}))
    assert "hotwords_file" not in calls[0] and backend["hotwords"] == "disabled_greedy_search"


def test_hotword_init_failure_keeps_the_engine_and_reports_it(monkeypatch, assets):
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {
        "enabled": True, "entries": [{"heard": "mu xing", "canonical": "暮星"}]})
    calls = install_fake_sherpa(monkeypatch, reject_hotwords=True)
    backend = stt_sherpa.build(stt_sherpa.validate({}))
    assert backend["backend"] == "sherpa_onnx" and backend["hotwords"] == "error:RuntimeError"
    assert len(calls) == 2 and "hotwords_file" not in calls[1]             # 同一引擎重试，不是换引擎
    assert stt_local._effective({**backend, "key": ()})["hotwords"] == "error:RuntimeError"


# ── model download: explicit, verified, tamper-evident ─────────────────────────

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(payloads, seen=None, fail_on=None):
    def opener(request, timeout=None):
        name = request.full_url.rsplit("/", 1)[-1]
        if seen is not None:
            seen.append(request.full_url)
        if fail_on == name:
            raise OSError("connection reset")
        return FakeResponse(payloads[name])
    return opener


def test_download_fetches_verified_files_from_the_pinned_commit(sandbox):
    seen = []
    payloads = {FAKE_NAMES[role]: data for role, data in FAKE_FILES.items()}
    stt_sherpa.download_model("fake-model", "https://mirror.example/", opener=fake_opener(payloads, seen))
    assert all(url.startswith("https://mirror.example/owner/fake/resolve/c0ffee/") for url in seen) and len(seen) == 5
    assert stt_sherpa.asset_status("fake-model")["present"] is True
    assert stt_sherpa.download_status()["state"] == "done"
    assert not list(stt_sherpa.model_dir("fake-model").glob("*.part"))


def test_download_is_idempotent_and_only_fetches_what_is_missing(sandbox):
    payloads = {FAKE_NAMES[role]: data for role, data in FAKE_FILES.items()}
    stt_sherpa.download_model("fake-model", opener=fake_opener(payloads))
    (stt_sherpa.model_dir("fake-model") / FAKE_NAMES["joiner"]).unlink()
    seen = []
    stt_sherpa.download_model("fake-model", opener=fake_opener(payloads, seen))
    assert [url.rsplit("/", 1)[-1] for url in seen] == [FAKE_NAMES["joiner"]]


def test_tampered_download_is_discarded_and_fails_loudly(sandbox):
    payloads = {FAKE_NAMES[role]: data for role, data in FAKE_FILES.items()}
    payloads[FAKE_NAMES["encoder"]] = b"Z" * len(FAKE_FILES["encoder"])        # 大小对，内容被换
    with pytest.raises(stt_local.SttLocalError, match="校验失败"):
        stt_sherpa.download_model("fake-model", opener=fake_opener(payloads))
    target = stt_sherpa.model_dir("fake-model")
    assert not (target / FAKE_NAMES["encoder"]).exists() and not list(target.glob("*.part"))
    status = stt_sherpa.download_status()
    assert status["state"] == "error" and "校验失败" in status["error"]


def test_network_failure_names_the_file_and_the_mirror_option_and_keeps_nothing_half_written(sandbox):
    payloads = {FAKE_NAMES[role]: data for role, data in FAKE_FILES.items()}
    with pytest.raises(stt_local.SttLocalError, match=r"下载 .* 失败.*download_base"):
        stt_sherpa.download_model("fake-model", opener=fake_opener(payloads, fail_on=FAKE_NAMES["joiner"]))
    assert not list(stt_sherpa.model_dir("fake-model").glob("*.part"))


def test_background_download_is_single_flight_and_reports_state(sandbox, monkeypatch):
    import threading
    gate = threading.Event()
    payloads = {FAKE_NAMES[role]: data for role, data in FAKE_FILES.items()}
    inner = fake_opener(payloads)

    def slow(request, timeout=None):
        gate.wait(5)
        return inner(request, timeout)
    monkeypatch.setattr(stt_sherpa, "_opener", lambda: slow)
    assert stt_sherpa.start_download("fake-model") is True
    assert stt_sherpa.start_download("fake-model") is False                    # 单飞
    assert stt_sherpa.download_status()["state"] == "running"
    gate.set()
    stt_sherpa._download_thread.join(10)
    assert stt_sherpa.download_status()["state"] == "done"


def test_unknown_model_download_ends_in_error_not_stuck_running(sandbox):
    assert stt_sherpa.start_download("nope") is True
    stt_sherpa._download_thread.join(10)
    status = stt_sherpa.download_status()
    assert status["state"] == "error" and "未知" in status["error"]


def test_asset_status_hashes_once_and_trusts_the_sidecar_until_the_file_changes(assets, monkeypatch):
    assert stt_sherpa.asset_status("fake-model", verify_hash=True)["present"] is True
    hashed = []
    real = stt_sherpa._sha256
    monkeypatch.setattr(stt_sherpa, "_sha256", lambda path: hashed.append(path.name) or real(path))
    assert stt_sherpa.asset_status("fake-model", verify_hash=True)["present"] is True
    assert hashed == []                                                         # 未变化：不重复哈希
    (assets / FAKE_NAMES["decoder"]).write_bytes(b"Q" * len(FAKE_FILES["decoder"]))
    assert stt_sherpa.asset_status("fake-model", verify_hash=True)["corrupt"] == [FAKE_NAMES["decoder"]]


def test_models_live_under_the_sandboxed_stt_model_dir(sandbox):
    from core.sandbox import get_paths
    assert stt_sherpa.model_dir("fake-model").parent == get_paths().stt_model_dir()
    assert "cache" in get_paths().stt_model_dir().parts


# ── probing is read-only ───────────────────────────────────────────────────────

def test_probe_reports_install_and_model_state_without_loading_anything(monkeypatch, sandbox):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", None)
    monkeypatch.setattr(stt_sherpa, "installed_version", lambda: None)
    info = stt_sherpa.probe()
    assert info["installed"] is False and "pip install sherpa-onnx" in info["hint"]
    monkeypatch.setattr(stt_sherpa, "installed_version", lambda: "1.13.8")
    info = stt_sherpa.probe()
    assert info["installed"] and info["models"]["fake-model"]["present"] is False and "下载模型" in info["hint"]
    assert stt_local._active is None


def test_hardware_probe_includes_the_sherpa_block(monkeypatch, sandbox):
    monkeypatch.setattr(stt_sherpa, "installed_version", lambda: None)
    assert stt_local.probe_hardware()["sherpa_onnx"]["installed"] is False


# ── /transcribe integration ────────────────────────────────────────────────────

def _backend(rec):
    return {"model": rec, "backend": "sherpa_onnx", "model_size": "fake-model", "device": "cpu",
            "compute_type": "int8", "fallback": None, "sample_rate": 16000}


def test_transcribe_uses_the_sherpa_branch_corrects_vocabulary_and_logs_the_engine(monkeypatch):
    import numpy as np
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {
        "enabled": True, "entries": [{"heard": "mu xing", "canonical": "暮星"}]})
    rec = FakeRecognizer({}, endpoints={2: "我叫 mu xing"}, steps=3)
    monkeypatch.setattr(stt_local, "get_backend", lambda cfg=None: _backend(rec))
    monkeypatch.setattr(stt_sherpa, "_decode_audio", lambda path, rate: np.zeros(16000, dtype="float32"))
    rows = []
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: rows.append(kw))
    assert transcribe_mod._transcribe_sync("clip.wav") == "我叫 暮星"
    assert rows[0]["provider"] == "sherpa_onnx" and rows[0]["model"] == "fake-model/cpu/int8" and rows[0]["ok"] is True
    assert rows[0]["purpose"] == "transcribe_local"


def test_sherpa_never_reaches_the_openai_whisper_branch(monkeypatch):
    import numpy as np

    class Whisper:
        def transcribe(self, *a, **k):
            raise AssertionError("must not be called")

    backend = {**_backend(FakeRecognizer({}, endpoints={1: "你好"}, steps=2)), "model_whisper": Whisper()}
    monkeypatch.setattr(stt_local, "get_backend", lambda cfg=None: backend)
    monkeypatch.setattr(stt_sherpa, "_decode_audio", lambda path, rate: np.zeros(16000, dtype="float32"))
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: None)
    assert transcribe_mod._transcribe_sync("clip.wav") == "你好"


def test_sherpa_output_that_is_a_wordlist_echo_is_dropped_like_the_other_engines(monkeypatch):
    import numpy as np
    monkeypatch.setattr("core.stt_vocabulary.settings", lambda config=None: {"enabled": True, "entries": [
        {"heard": "mu xing", "canonical": "暮星"}, {"heard": "xing ye", "canonical": "星野"}]})
    rec = FakeRecognizer({}, endpoints={1: "暮星 星野"}, steps=2)
    monkeypatch.setattr(stt_local, "get_backend", lambda cfg=None: _backend(rec))
    monkeypatch.setattr(stt_sherpa, "_decode_audio", lambda path, rate: np.zeros(16000, dtype="float32"))
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: None)
    assert transcribe_mod._transcribe_sync("clip.wav") == ""


def test_the_repeat_threshold_comes_from_the_saved_settings(monkeypatch):
    import numpy as np
    rec = FakeRecognizer({}, endpoints={1: "对对对"}, steps=2)
    monkeypatch.setattr(stt_local, "get_backend", lambda cfg=None: _backend(rec))
    monkeypatch.setattr(stt_local, "settings", lambda config=None: sherpa_cfg(repeat_collapse_min_run=3))
    monkeypatch.setattr(stt_sherpa, "_decode_audio", lambda path, rate: np.zeros(16000, dtype="float32"))
    monkeypatch.setattr("core.api_call_log.append", lambda **kw: None)
    assert transcribe_mod._transcribe_sync("clip.wav") == "对对"


# ── warmup selection ───────────────────────────────────────────────────────────

def test_warmup_for_sherpa_needs_both_the_package_and_the_downloaded_model(monkeypatch, assets):
    monkeypatch.setattr("core.audio_perception.config", lambda: {"enabled": False, "presets": {}, "routes": {}})
    monkeypatch.setattr(stt_local, "settings", lambda config=None: sherpa_cfg())
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    monkeypatch.setattr(stt_local.importlib.util, "find_spec", lambda name: object() if name == "sherpa_onnx" else None)
    assert stt_local._local_stt_selected() is True
    monkeypatch.setattr(stt_local.importlib.util, "find_spec", lambda name: None)
    assert stt_local._local_stt_selected() is False
    monkeypatch.setattr(stt_local.importlib.util, "find_spec", lambda name: object() if name == "sherpa_onnx" else None)
    (assets / FAKE_NAMES["tokens"]).unlink()
    assert stt_local._local_stt_selected() is False
