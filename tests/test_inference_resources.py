import asyncio

import pytest

from core import inference_resources as gpu


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(gpu, "get_config", lambda: {"inference_gpu_limits": {
        "shared": {"origins": ["http://gpu-one:9000", "http://gpu-one:9001"], "max_concurrency": 1},
        "independent": {"origins": ["http://gpu-two:9000"], "max_concurrency": 2},
    }})


def test_gpu_limits_use_origins_and_independent_capacity(limits):
    with gpu.reserve("http://gpu-one:9000/v1"):
        with pytest.raises(gpu.GpuResourceBusy, match="gpu_group_busy"):
            with gpu.reserve("http://gpu-one:9001/tts"):
                pass
        with gpu.reserve("http://gpu-two:9000/v1"), gpu.reserve("http://gpu-two:9000/v1"):
            assert gpu.snapshot()["active"] == {"shared": 1, "independent": 2}
    assert gpu.snapshot()["active"] == {}


@pytest.mark.asyncio
async def test_gpu_cancellation_retains_slot_until_outbound_finishes(limits):
    entered, done = asyncio.Event(), asyncio.Event()

    async def outbound():
        entered.set()
        await done.wait()
        raise ConnectionError("test disconnect")

    async def request():
        with gpu.reserve("http://gpu-one:9000") as slot:
            await slot.run(outbound())

    task = asyncio.create_task(request())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert gpu.snapshot()["active"] == {"shared": 1}
    finally:
        done.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert gpu.snapshot()["active"] == {}


@pytest.mark.asyncio
async def test_gpu_slot_follows_recovery_after_cancelled_caller(limits):
    started, timeout, ready = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def request():
        with gpu.reserve("http://gpu-one:9000") as slot:
            async def outbound():
                started.set()
                await timeout.wait()
                slot.defer_until(asyncio.create_task(ready.wait()))
                raise TimeoutError

            await slot.run(outbound())

    task = asyncio.create_task(request())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    timeout.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert gpu.snapshot()["active"] == {"shared": 1}
    ready.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert gpu.snapshot()["active"] == {}


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError()])
@pytest.mark.asyncio
async def test_gpu_failed_outbound_releases_slot(limits, error):
    async def outbound():
        raise error

    with pytest.raises(type(error)):
        with gpu.reserve("http://gpu-one:9000") as slot:
            await slot.run(outbound())
    with gpu.reserve("http://gpu-one:9001"):
        assert gpu.snapshot()["active"] == {"shared": 1}


def test_rejects_overlapping_origins():
    with pytest.raises(ValueError, match="only one GPU group"):
        gpu.validate_limits({"a": {"origins": ["https://gpu.example"], "max_concurrency": 1},
                             "b": {"origins": ["https://gpu.example:443"], "max_concurrency": 1}})
