"""Local STT engine #2: sherpa-onnx streaming zipformer transducer, used for whole-clip transcription.

``stt_local.engine = sherpa_onnx`` selects this engine (default stays ``faster_whisper``).  It is the
sibling of the faster-whisper path in ``core/stt_local.py`` and shares its lifecycle (hot swap, smoke
test, ``api_call_log`` metrics); everything sherpa-specific lives here.

Design decisions (工单 H, see docs/audio-perception.md):

* **No silent fallback.**  A missing package, missing/corrupt model file or failed smoke test raises
  ``SttLocalError`` with an actionable message.  Someone who picked this engine because Whisper felt slow
  must never end up on Whisper again without being told.
* **Repeat protection (the "单字重复" complaint).**  Transducer decoders can emit the same token over and
  over on silence or weak audio, and the input-method app that motivated this engine ran with endpointing
  off, greedy search and no post-processing.  Here endpoint detection is ON (the clip is cut at pauses
  instead of decoded as one endless stream), search is ``modified_beam_search`` (also required by hotwords)
  and ``collapse_repeats`` is the last line of defence.
* **Hotwords** reuse ``stt_vocabulary.hotwords()``; they are written to a hotwords file passed to the
  recognizer constructor (the documented ``hotwords_file`` parameter), so a vocabulary change is part of the
  engine key and rebuilds the recognizer.  Hotwords only work with ``modified_beam_search``.
* **Model weights are not in git.**  They are downloaded on an explicit admin action (about 200 MB, far
  beyond a request timeout), SHA-256 verified file by file, and stored under ``get_paths().stt_model_dir()``.

Offline transcription only; streaming over the wire would change the recording protocol on three repos.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ENGINE = "sherpa_onnx"
DECODING_METHODS = ("modified_beam_search", "greedy_search")
DEFAULT_DOWNLOAD_BASE = "https://huggingface.co"

# One verified entry.  The model is the one the owner already runs in their input method and called
# "ok"; the pinned commit and per-file SHA-256 below make the download tamper-evident.  Newer models
# exist (a 700 MB Chinese-only xlarge, a 2026 third-party zh-en line); see docs/audio-perception.md for
# why they are not the default.  Add an entry here to offer another model.
MODELS: dict[str, dict[str, Any]] = {
    "zipformer-bilingual-zh-en-2023-02-20": {
        "label": "streaming zipformer 中英双语（2023-02-20，int8）",
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "commit": "98590b7ed6443e77b714204da2757d75e1a642f4",
        "modeling_unit": "cjkchar+bpe",
        "sample_rate": 16000,
        "feature_dim": 80,
        "files": {
            "encoder": {"name": "encoder-epoch-99-avg-1.int8.onnx", "size": 181895032,
                        "sha256": "8fa764187a261844f859d7143ebaa563af5d10adfece4c18a8f414c88cba2a9b"},
            "decoder": {"name": "decoder-epoch-99-avg-1.onnx", "size": 13876452,
                        "sha256": "2e3b5ec371f8899ee6acd829fd753ba45772df57a91bdf37cde3136354e7db7d"},
            "joiner": {"name": "joiner-epoch-99-avg-1.int8.onnx", "size": 3228404,
                       "sha256": "1ed689c5ed19dbaa725d9d191bb4822b5f4855a39e1ffd28cbc1f340d25b2ee0"},
            "tokens": {"name": "tokens.txt", "size": 56317,
                       "sha256": "a8e0e4ec53810e433789b54a5c0134a7eaa2ffca595a6334d54c00da858841d3"},
            "bpe_vocab": {"name": "bpe.vocab", "size": 12564,
                          "sha256": "d0b642f3a2eacd5fadefdeff9e0e1358cab729647cbb7fe58cf738e1f7407029"},
        },
    },
}
DEFAULT_MODEL = "zipformer-bilingual-zh-en-2023-02-20"

DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "num_threads": 2,
    "decoding_method": "modified_beam_search",
    "max_active_paths": 4,
    "hotwords_score": 1.5,
    # Trailing silence (seconds) that closes a segment once speech was heard (sherpa "rule 2").
    "endpoint_silence_seconds": 0.8,
    # Runs of one identical character/word of at least this length are folded to two.  Legitimate Chinese
    # reduplication (好好 / 看看) and short repeats (对对对 / 哈哈哈) stay untouched below the default of 4.
    "repeat_collapse_min_run": 4,
    "download_base": DEFAULT_DOWNLOAD_BASE,
}


# ── settings ─────────────────────────────────────────────────────────────────

def _number(block: dict, key: str, low: float, high: float, *, integer: bool) -> float | int:
    value = block[key]
    kind = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kind) or not low <= value <= high:
        raise ValueError(f"sherpa_onnx.{key} 必须是 {low:g}–{high:g} 的{'整数' if integer else '数'}")
    return value if integer else float(value)


def validate(payload: Any) -> dict[str, Any]:
    """Strict validation of the ``sherpa_onnx`` group; raises ValueError naming the bad field."""
    block = payload if isinstance(payload, dict) else {}
    out = dict(DEFAULTS)
    if "model" in block:
        if block["model"] not in MODELS:
            raise ValueError(f"sherpa_onnx.model 必须是 {'/'.join(MODELS)}")
        out["model"] = block["model"]
    if "decoding_method" in block:
        if block["decoding_method"] not in DECODING_METHODS:
            raise ValueError(f"sherpa_onnx.decoding_method 必须是 {'/'.join(DECODING_METHODS)}")
        out["decoding_method"] = block["decoding_method"]
    for key, low, high, integer in (
        ("num_threads", 1, 16, True), ("max_active_paths", 1, 16, True),
        ("hotwords_score", 0, 10, False), ("endpoint_silence_seconds", 0.3, 5, False),
        ("repeat_collapse_min_run", 3, 20, True),
    ):
        if key in block:
            out[key] = _number(block, key, low, high, integer=integer)
    if "download_base" in block:
        base = str(block["download_base"] or "").strip().rstrip("/")
        try:
            parts = urlsplit(base)
        except ValueError as error:
            raise ValueError("sherpa_onnx.download_base 不是合法地址") from error
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password \
                or parts.query or parts.fragment:
            raise ValueError("sherpa_onnx.download_base 必须是不带账号密码和查询串的 http(s) 地址")
        out["download_base"] = base
    return out


def key_fields(cfg: dict[str, Any]) -> tuple:
    """Everything that requires rebuilding the recognizer when it changes (download_base does not)."""
    return (cfg["model"], cfg["num_threads"], cfg["decoding_method"], cfg["max_active_paths"],
            cfg["hotwords_score"], cfg["endpoint_silence_seconds"])


# ── hotwords ─────────────────────────────────────────────────────────────────

def hotword_phrases(config: dict[str, Any] | None = None) -> list[str]:
    """Canonical vocabulary words, one phrase each.  Reuses ``stt_vocabulary.hotwords()``; the model
    emits upper-case English, so ASCII is upper-cased for matching (``stt_vocabulary.correct`` restores the
    owner's spelling afterwards)."""
    from core.stt_vocabulary import hotwords
    raw = hotwords(config)
    seen: dict[str, None] = {}
    for item in re.split(r"\s*,\s*", raw):
        item = item.strip()
        if item:
            seen[item.upper() if item.isascii() else item] = None
    return list(seen)


def hotwords_signature(config: dict[str, Any] | None = None) -> str:
    phrases = hotword_phrases(config)
    return hashlib.sha256("\n".join(phrases).encode("utf-8")).hexdigest()[:12] if phrases else ""


# ── text post-processing ─────────────────────────────────────────────────────

_ASCII_UPPER_WORD = re.compile(r"[A-Z]{4,}(?:'[A-Z]+)?")


def collapse_repeats(text: str, min_run: int = DEFAULTS["repeat_collapse_min_run"]) -> str:
    """Fold abnormal repetition: ``min_run`` or more identical consecutive letters/characters become two,
    and so do identical consecutive space-separated words.  Anything shorter is left alone on purpose, and
    digits and punctuation are never touched (``1000000``, ``……``).

    The threshold is the compromise between the two failure modes: the decoder bug produces dozens of the
    same token, while real speech has 好好/看看 (2) and 对对对/哈哈哈 (3).  A genuine laugh of four or more
    loses its length, which is acceptable; a legitimate "看看" never does.
    """
    if not text or min_run < 2:
        return text
    text = re.sub(r"([^\W\d_])\1{%d,}" % (min_run - 1), lambda m: m.group(1) * 2, text)
    folded: list[str] = []
    for word, group in itertools.groupby(text.split(" ")):
        count = len(list(group))
        folded.extend([word] * (2 if word and count >= min_run else count))
    return " ".join(folded)


def _smart_join(parts: list[str]) -> str:
    """Join endpoint-separated segments: no space between CJK neighbours, a space around Latin words."""
    result = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if result and result[-1].isascii() and part[0].isascii() and not result[-1].isspace():
            result += " "
        result += part
    return result


def normalize_text(text: str, repeat_min_run: int = DEFAULTS["repeat_collapse_min_run"]) -> str:
    """The model emits UPPER-CASE English with no case information.  Words of four or more letters are
    lower-cased so chat does not shout; shorter ones (AI, USB, GPT) stay as they are, and
    ``stt_vocabulary.correct`` restores the owner's own spellings afterwards."""
    text = _ASCII_UPPER_WORD.sub(lambda m: m.group(0).lower(), text.strip())
    return collapse_repeats(text, repeat_min_run).strip()


# ── model assets (explicit download, SHA-256 verified) ───────────────────────

class _AssetError(Exception):
    """Internal: converted to SttLocalError by the callers that own the user-facing message."""


def _stt_error(message: str) -> Exception:
    from core.stt_local import SttLocalError
    return SttLocalError(message)


def model_dir(model_id: str) -> Path:
    from core.sandbox import get_paths
    return get_paths().stt_model_dir() / model_id


def _file_path(model_id: str, role: str) -> Path:
    return model_dir(model_id) / MODELS[model_id]["files"][role]["name"]


def asset_status(model_id: str, *, verify_hash: bool = False) -> dict[str, Any]:
    """Cheap by default (existence + size).  ``verify_hash`` also checks SHA-256, caching the verdict in a
    sidecar keyed by (size, mtime) so a 180 MB encoder is hashed once, not on every load."""
    spec = MODELS[model_id]
    missing: list[str] = []
    bad: list[str] = []
    total = 0
    sidecar_path = model_dir(model_id) / ".verified.json"
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sidecar = {}
    for role, item in spec["files"].items():
        path = _file_path(model_id, role)
        if not path.is_file():
            missing.append(item["name"])
            continue
        stat = path.stat()
        total += stat.st_size
        if stat.st_size != item["size"]:
            bad.append(item["name"])
            continue
        if verify_hash:
            stamp = [stat.st_size, stat.st_mtime_ns]
            if sidecar.get(item["name"]) == stamp:
                continue
            if _sha256(path) != item["sha256"]:
                bad.append(item["name"])
                continue
            sidecar[item["name"]] = stamp
    if verify_hash and not missing and not bad:
        try:
            sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        except OSError:
            pass
    return {"model": model_id, "label": spec["label"], "present": not missing and not bad,
            "missing": missing, "corrupt": bad, "bytes": total,
            "expected_bytes": sum(item["size"] for item in spec["files"].values())}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_download_lock = threading.Lock()
_download: dict[str, Any] = {"state": "idle", "model": "", "file": "", "bytes_done": 0, "bytes_total": 0, "error": ""}
_download_thread: threading.Thread | None = None


def download_status() -> dict[str, Any]:
    with _download_lock:
        return dict(_download)


def _set_download(**changes: Any) -> None:
    with _download_lock:
        _download.update(changes)


def _opener() -> Callable:
    """Honour the project proxy setting for the download, else the process environment."""
    try:
        from core.proxy_config import get_aiohttp_proxy
        proxy = get_aiohttp_proxy()
    except Exception:  # noqa: BLE001 - proxy lookup must never block a download
        proxy = None
    if proxy:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy})).open
    return urllib.request.urlopen


def download_model(model_id: str, base: str = DEFAULT_DOWNLOAD_BASE, *, opener: Callable | None = None) -> None:
    """Fetch every missing/corrupt file, verifying size and SHA-256 before it is moved into place.

    Blocking; raises SttLocalError.  A file that fails verification is deleted, never kept half-trusted.
    """
    if model_id not in MODELS:
        raise _stt_error(f"未知的 sherpa-onnx 模型：{model_id}")
    spec = MODELS[model_id]
    opener = opener or _opener()
    target = model_dir(model_id)
    target.mkdir(parents=True, exist_ok=True)
    todo = [(role, item) for role, item in spec["files"].items()
            if not (_file_path(model_id, role).is_file()
                    and _file_path(model_id, role).stat().st_size == item["size"]
                    and _sha256(_file_path(model_id, role)) == item["sha256"])]
    total = sum(item["size"] for _role, item in todo)
    done = 0
    _set_download(state="running", model=model_id, file="", bytes_done=0, bytes_total=total, error="")
    for _role, item in todo:
        url = f"{base.rstrip('/')}/{spec['repo']}/resolve/{spec['commit']}/{item['name']}"
        dest = target / item["name"]
        part = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        size = 0
        _set_download(file=item["name"])
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "PresenceKit/1.0"})
            with opener(request, timeout=30) as response, open(part, "wb") as out:
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    _set_download(bytes_done=done + size)
        except Exception as error:  # noqa: BLE001 - reported verbatim, nothing half-written is kept
            part.unlink(missing_ok=True)
            message = f"下载 {item['name']} 失败：{type(error).__name__}（可在配置里改 download_base 使用镜像）"
            _set_download(state="error", error=message)
            raise _stt_error(message) from error
        if size != item["size"] or digest.hexdigest() != item["sha256"]:
            part.unlink(missing_ok=True)
            message = f"{item['name']} 校验失败（大小或 SHA-256 不符），已丢弃；请检查下载源是否被篡改或重试"
            _set_download(state="error", error=message)
            raise _stt_error(message)
        os.replace(part, dest)
        done += size
    asset_status(model_id, verify_hash=True)
    _set_download(state="done", file="", bytes_done=total, error="")


def start_download(model_id: str, base: str = DEFAULT_DOWNLOAD_BASE) -> bool:
    """Run ``download_model`` on a daemon thread.  Returns False when a download is already running."""
    global _download_thread
    with _download_lock:
        if _download["state"] == "running":
            return False
        _download.update(state="running", model=model_id, file="", bytes_done=0, bytes_total=0, error="")

    def _run() -> None:
        try:
            download_model(model_id, base)
        except Exception as error:  # noqa: BLE001 - reported through the shared state
            logger.warning("[stt_sherpa] 模型下载失败: %s", error)
            if download_status()["state"] == "running":      # failed before any file was attempted
                _set_download(state="error", error=str(error))

    _download_thread = threading.Thread(target=_run, name="sherpa-model-download", daemon=True)
    _download_thread.start()
    return True


def reset_for_tests() -> None:
    global _download_thread
    thread, _download_thread = _download_thread, None
    if thread is not None:
        thread.join(timeout=10)
    _set_download(state="idle", model="", file="", bytes_done=0, bytes_total=0, error="")


# ── probing (read-only; never imports the heavy runtime beyond find_spec) ────

def installed_version() -> str | None:
    import importlib.util
    if importlib.util.find_spec("sherpa_onnx") is None:
        return None
    try:
        from importlib.metadata import version
        return version("sherpa-onnx")
    except Exception:  # noqa: BLE001
        return "unknown"


def probe() -> dict[str, Any]:
    """For the admin page: is the package there, and which models are on disk.  Loads nothing."""
    version = installed_version()
    info: dict[str, Any] = {
        "installed": version is not None, "version": version or "",
        "models": {model_id: asset_status(model_id) for model_id in MODELS},
        "download": download_status(),
        "hint": "",
    }
    if version is None:
        info["hint"] = "未安装 sherpa-onnx：在运行环境执行 pip install sherpa-onnx（支持 Python 3.10–3.12）。"
    elif not any(item["present"] for item in info["models"].values()):
        info["hint"] = "已安装 sherpa-onnx，但模型文件还没下载：在本页点击「下载模型」（约 200 MB）。"
    return info


# ── recognizer ───────────────────────────────────────────────────────────────

def _decode_audio(audio_path: str, sample_rate: int):
    """File -> mono float32 at ``sample_rate``.  Reuses faster-whisper's PyAV decoder (any container/codec
    the browser records), else plain WAV via the standard library."""
    import numpy as np
    try:
        from faster_whisper.audio import decode_audio
        return decode_audio(audio_path, sampling_rate=sample_rate)
    except ImportError:
        pass
    import wave
    try:
        with wave.open(audio_path, "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise _stt_error("解码音频需要 faster-whisper 自带的解码器（pip install faster-whisper），"
                         "没有它时 sherpa-onnx 只能读取 WAV 文件") from error
    if width != 2:
        raise _stt_error("没有 faster-whisper 解码器时只支持 16-bit PCM WAV")
    samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != sample_rate:
        positions = np.linspace(0, len(samples) - 1, int(len(samples) * sample_rate / rate))
        samples = np.interp(positions, np.arange(len(samples)), samples).astype("float32")
    return samples


def recognize(recognizer, samples, sample_rate: int = 16000, *, repeat_min_run: int = DEFAULTS["repeat_collapse_min_run"]) -> str:
    """Run one clip through an OnlineRecognizer with endpoint segmentation.

    Pure over the recognizer interface (create_stream / is_ready / decode_stream / is_endpoint / reset /
    get_result), so it is unit-tested against a fake.
    """
    import numpy as np
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    # Tail padding lets the last frames flush; without it the final syllable is often dropped.
    stream.accept_waveform(sample_rate, np.zeros(int(0.66 * sample_rate), dtype=np.float32))
    stream.input_finished()
    segments: list[str] = []
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
        if recognizer.is_endpoint(stream):
            text = str(recognizer.get_result(stream) or "").strip()
            if text:
                segments.append(text)
            recognizer.reset(stream)
    tail = str(recognizer.get_result(stream) or "").strip()
    if tail:
        segments.append(tail)
    return normalize_text(_smart_join(segments), repeat_min_run)


def _write_hotwords_file(model_id: str, phrases: list[str]) -> Path | None:
    if not phrases:
        return None
    path = model_dir(model_id) / f"hotwords-{hotwords_signature()}.txt"
    path.write_text("\n".join(phrases) + "\n", encoding="utf-8", newline="\n")
    return path


def build(cfg: dict[str, Any]) -> dict[str, Any]:
    """Construct and smoke-test the recognizer for the ``sherpa_onnx`` settings group.

    Returns the backend dict shaped like the faster-whisper one (``admin/routers/transcribe.py`` reads
    backend / model_size / device / compute_type / fallback for the api_call_log row).
    """
    try:
        import numpy as np
        import sherpa_onnx
    except ImportError as error:
        raise _stt_error("未安装 sherpa-onnx：在运行环境执行 pip install sherpa-onnx（支持 Python 3.10–3.12）") from error
    model_id = cfg["model"]
    spec = MODELS[model_id]
    status = asset_status(model_id, verify_hash=True)
    if not status["present"]:
        problems = [f"缺少 {name}" for name in status["missing"]] + [f"{name} 校验不通过" for name in status["corrupt"]]
        raise _stt_error(f"sherpa-onnx 模型 {model_id} 不可用（{'；'.join(problems)}）。"
                         "请在「本地模型运行」页点击「下载模型」，不会自动回落到 Whisper")
    files = {role: str(_file_path(model_id, role)) for role in spec["files"]}
    greedy = cfg["decoding_method"] != "modified_beam_search"
    endpoint = float(cfg["endpoint_silence_seconds"])
    kwargs: dict[str, Any] = dict(
        tokens=files["tokens"], encoder=files["encoder"], decoder=files["decoder"], joiner=files["joiner"],
        num_threads=int(cfg["num_threads"]), sample_rate=spec["sample_rate"], feature_dim=spec["feature_dim"],
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=max(2.4, endpoint * 3), rule2_min_trailing_silence=endpoint,
        rule3_min_utterance_length=20.0,
        decoding_method=cfg["decoding_method"], max_active_paths=int(cfg["max_active_paths"]),
        provider="cpu",
    )
    phrases = [] if greedy else hotword_phrases()
    hotwords_state = "none" if not hotword_phrases() else ("disabled_greedy_search" if greedy else "active")
    recognizer = None
    if phrases:
        hotword_file = _write_hotwords_file(model_id, phrases)
        try:
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                **kwargs, hotwords_file=str(hotword_file), hotwords_score=float(cfg["hotwords_score"]),
                modeling_unit=spec["modeling_unit"], bpe_vocab=files["bpe_vocab"])
        except Exception as error:  # noqa: BLE001 - biasing is optional; the engine itself is not
            hotwords_state = f"error:{type(error).__name__}"
            logger.warning("[stt_sherpa] 热词偏置初始化失败，本次不带热词继续（引擎未切换）: %s", error)
    try:
        if recognizer is None:
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
        # One real, tiny inference: a recognizer that constructs can still fail at first decode.
        recognize(recognizer, np.zeros(8000, dtype=np.float32), spec["sample_rate"])
    except Exception as error:  # noqa: BLE001
        raise _stt_error(f"sherpa-onnx 加载或自检失败：{error}") from error
    return {"model": recognizer, "backend": ENGINE, "device": "cpu", "model_size": model_id,
            "compute_type": "int8", "fallback": None, "sample_rate": spec["sample_rate"],
            "hotwords": hotwords_state}


_decode_lock = threading.Lock()   # one shared recognizer; decodes are short, so serialise them


def transcribe(backend: dict[str, Any], audio_path: str, *, repeat_min_run: int = DEFAULTS["repeat_collapse_min_run"]) -> str:
    samples = _decode_audio(audio_path, backend["sample_rate"])
    with _decode_lock:
        return recognize(backend["model"], samples, backend["sample_rate"], repeat_min_run=repeat_min_run)
