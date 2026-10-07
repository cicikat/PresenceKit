import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("is_group", [False, True])
async def test_image_payload_contains_bytes_without_host_path(tmp_path, monkeypatch, is_group):
    from core import qq_adapter

    image = tmp_path / "image.png"
    image.write_bytes(b"fixture-image")
    socket = AsyncMock()
    socket.closed = False
    monkeypatch.setattr(qq_adapter, "_ws", socket)

    await qq_adapter.send_image("12345", str(image), is_group)

    frame = json.loads(socket.send_str.call_args.args[0])
    assert frame["action"] == ("send_group_msg" if is_group else "send_private_msg")
    segment = frame["params"]["message"][0]
    assert segment["type"] == "image"
    assert base64.b64decode(segment["data"]["file"].removeprefix("base64://")) == image.read_bytes()
    assert str(image) not in json.dumps(frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("conversion_fails", [False, True])
async def test_voice_payload_survives_container_boundary(monkeypatch, conversion_fails):
    import subprocess
    from core import qq_adapter
    from core.output import voice_adapter

    temporary_paths = []

    def convert(command, **kwargs):
        temporary_paths.extend([Path(command[3]), Path(command[-1])])
        if conversion_fails:
            raise subprocess.CalledProcessError(1, command)
        Path(command[-1]).write_bytes(b"fixture-amr")

    send = AsyncMock()
    monkeypatch.setattr(subprocess, "run", convert)
    monkeypatch.setattr(qq_adapter, "send_record", send)

    await voice_adapter.send_voice("12345", b"fixture-wav", True)

    target, payload, is_group = send.call_args.args
    assert (target, is_group) == ("12345", True)
    assert payload.startswith("base64://")
    assert base64.b64decode(payload[9:]) == (b"fixture-wav" if conversion_fails else b"fixture-amr")
    assert all(not path.exists() for path in temporary_paths)
