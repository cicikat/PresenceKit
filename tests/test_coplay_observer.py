"""
tests/test_coplay_observer.py — Brief 40 验收：
  - 差分剧变检测用合成帧序列做离线单元测试（不依赖真实截屏）。
  - OCR / VLM 均 fail-open。
  - moment 队列有上限防堆积。
"""

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from core.coplay import observer
from core.coplay.observer import GameMoment

UID = "u1"


def _png(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (160, 90), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png(seed: int) -> bytes:
    """合成一帧"剧变"帧：直方图检测只看颜色分布，纯随机噪声帧之间的整体颜色分布
    其实非常接近（都约等于均匀分布），骗不过阈值——所以这里改用每帧一个截然不同
    的纯色块，稳定制造高直方图差异，模拟"持续剧变"（例如战斗中的画面闪烁）。"""
    palette = [
        (250, 30, 30), (30, 250, 30), (30, 30, 250),
        (250, 250, 30), (30, 250, 250), (250, 30, 250),
    ]
    color = palette[seed % len(palette)]
    img = Image.new("RGB", (160, 90), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_observer_state():
    observer.clear_moments_for_test()
    observer.reset_diff_state_for_test()
    observer.reset_save_watch_for_test()
    observer.reset_ocr_engine_for_test()
    yield
    observer.clear_moments_for_test()
    observer.reset_diff_state_for_test()
    observer.reset_save_watch_for_test()
    observer.reset_ocr_engine_for_test()


# ═══════════════════════════════════════════════════════════════════════════
# 差分剧变检测（离线，合成帧序列）
# ═══════════════════════════════════════════════════════════════════════════

def test_classify_frame_first_call_returns_none():
    assert observer.classify_frame(UID, _png((10, 10, 10))) is None


def test_classify_frame_no_change_is_none():
    observer.classify_frame(UID, _png((10, 10, 10)))
    assert observer.classify_frame(UID, _png((10, 10, 10))) is None


def test_classify_frame_detects_scene_change():
    observer.classify_frame(UID, _png((10, 10, 10)))
    result = observer.classify_frame(UID, _png((250, 250, 250)))
    assert result == "scene_change"


def test_classify_frame_sustained_variance_triggers_combat_start():
    observer.classify_frame(UID, _noisy_png(0))
    results = [observer.classify_frame(UID, _noisy_png(i)) for i in range(1, 6)]
    assert "combat_start" in results


def test_classify_frame_combat_end_after_stabilizing():
    observer.classify_frame(UID, _noisy_png(0))
    results = [observer.classify_frame(UID, _noisy_png(i)) for i in range(1, 6)]
    assert "combat_start" in results

    stable = _png((5, 5, 5))
    observer.classify_frame(UID, stable)
    result = observer.classify_frame(UID, stable)
    assert result == "combat_end"


def test_classify_frame_idle_after_sustained_low_variance():
    stable = _png((5, 5, 5))
    observer.classify_frame(UID, stable)
    results = [observer.classify_frame(UID, stable) for _ in range(observer.IDLE_STREAK + 1)]
    assert "idle" in results
    # idle only reported once per streak, not every tick
    assert results.count("idle") == 1


def test_histogram_diff_ratio_identical_is_zero():
    frame = _png((100, 100, 100))
    assert observer.histogram_diff_ratio(frame, frame) == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# OCR — fail-open
# ═══════════════════════════════════════════════════════════════════════════

def test_ocr_frame_fail_open_when_engine_unavailable():
    with patch.object(observer, "_get_ocr_engine", return_value=None):
        assert observer.ocr_frame(_png((0, 0, 0))) == []


def test_ocr_frame_fail_open_on_engine_exception():
    fake_engine = lambda arr: (_ for _ in ()).throw(RuntimeError("boom"))
    with patch.object(observer, "_get_ocr_engine", return_value=fake_engine):
        assert observer.ocr_frame(_png((0, 0, 0))) == []


def test_classify_ocr_text_death():
    assert observer.classify_ocr_text(["YOU DIED", "press any key"]) == "death"


def test_classify_ocr_text_achievement():
    assert observer.classify_ocr_text(["成就达成：初出茅庐"]) == "achievement"


def test_classify_ocr_text_none():
    assert observer.classify_ocr_text(["库存", "背包"]) is None


# ═══════════════════════════════════════════════════════════════════════════
# VLM 兜底 — fail-open
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_vlm_fallback_none_when_vision_disabled():
    import core.config_loader as cl
    original = cl.get_config().get("vision")
    cl.get_config()["vision"] = {"enabled": False}
    try:
        result = await observer.vlm_fallback_summary(_png((1, 2, 3)))
    finally:
        if original is not None:
            cl.get_config()["vision"] = original
    assert result is None


@pytest.mark.asyncio
async def test_vlm_fallback_fail_open_on_exception():
    import core.config_loader as cl
    original = cl.get_config().get("vision")
    cl.get_config()["vision"] = {"enabled": True, "model": "test-vision"}
    try:
        with patch("core.llm_client.chat", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await observer.vlm_fallback_summary(_png((1, 2, 3)))
    finally:
        if original is not None:
            cl.get_config()["vision"] = original
    assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# moment 队列上限
# ═══════════════════════════════════════════════════════════════════════════

def test_moment_queue_has_cap():
    for i in range(observer.MOMENT_QUEUE_MAXLEN + 20):
        observer.push_moment(UID, GameMoment(kind="idle", summary=f"m{i}"))
    q = observer.peek_moments(UID)
    assert len(q) == observer.MOMENT_QUEUE_MAXLEN
    # 保留的是最新的（尾部），不是最早的
    assert q[-1].summary == f"m{observer.MOMENT_QUEUE_MAXLEN + 19}"


def test_drain_moments_empties_queue():
    observer.push_moment(UID, GameMoment(kind="idle", summary="x"))
    drained = observer.drain_moments(UID)
    assert len(drained) == 1
    assert observer.peek_moments(UID) == []


# ═══════════════════════════════════════════════════════════════════════════
# 存档 watch
# ═══════════════════════════════════════════════════════════════════════════

def test_check_save_point_first_call_is_false(tmp_path):
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    (save_dir / "slot1.sav").write_text("a")
    assert observer.check_save_point(UID, str(save_dir)) is False


def test_check_save_point_detects_mtime_change(tmp_path):
    import os
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    f = save_dir / "slot1.sav"
    f.write_text("a")
    observer.check_save_point(UID, str(save_dir))  # baseline (first call)

    new_time = (f.stat().st_mtime) + 10
    os.utime(f, (new_time, new_time))
    assert observer.check_save_point(UID, str(save_dir)) is True


def test_check_save_point_missing_dir_is_false(tmp_path):
    assert observer.check_save_point(UID, str(tmp_path / "does_not_exist")) is False


# ═══════════════════════════════════════════════════════════════════════════
# tick() 编排
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tick_ocr_death_takes_priority_over_diff():
    with patch.object(observer, "_capture_ready", return_value=True), \
         patch.object(observer, "capture_screen", return_value=_png((1, 1, 1))), \
         patch.object(observer, "ocr_frame", return_value=["YOU DIED"]), \
         patch.object(observer, "classify_frame", return_value="scene_change") as mock_diff, \
         patch.object(observer, "vlm_fallback_summary", new=AsyncMock()) as mock_vlm:
        await observer.tick(UID)

    moments = observer.peek_moments(UID)
    assert len(moments) == 1
    assert moments[0].kind == "death"
    mock_vlm.assert_not_called()  # OCR 命中时不该再兜底调 VLM


@pytest.mark.asyncio
async def test_tick_scene_change_calls_vlm_fallback():
    with patch.object(observer, "_capture_ready", return_value=True), \
         patch.object(observer, "capture_screen", return_value=_png((1, 1, 1))), \
         patch.object(observer, "ocr_frame", return_value=[]), \
         patch.object(observer, "classify_frame", return_value="scene_change"), \
         patch.object(observer, "vlm_fallback_summary", new=AsyncMock(return_value="她正站在悬崖边")):
        await observer.tick(UID)

    moments = observer.peek_moments(UID)
    assert len(moments) == 1
    assert moments[0].kind == "scene_change"
    assert moments[0].summary == "她正站在悬崖边"


@pytest.mark.asyncio
async def test_tick_noop_when_capture_not_ready():
    with patch.object(observer, "_capture_ready", return_value=False), \
         patch.object(observer, "capture_screen") as mock_capture:
        await observer.tick(UID)
    mock_capture.assert_not_called()
    assert observer.peek_moments(UID) == []


@pytest.mark.asyncio
async def test_tick_noop_when_capture_fails():
    with patch.object(observer, "_capture_ready", return_value=True), \
         patch.object(observer, "capture_screen", return_value=None):
        await observer.tick(UID)
    assert observer.peek_moments(UID) == []
