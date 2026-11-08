import base64

import httpx
import pytest

from core import runware_images


@pytest.fixture
def transport(monkeypatch):
    state = {"tasks": [], "closed": 0}
    class Client:
        async def post(self, url, headers, json, follow_redirects):
            assert not follow_redirects
            assert url == "https://api.runware.ai/v1"
            assert headers["Authorization"] == "Bearer fixture-key"
            task = json[0]
            state["tasks"].append(task)
            if state.get("error"):
                payload = {"errors": [{"code": "insufficientCredits", "message": "secret=fixture-key"}]}
            elif task["taskType"] == "modelSearch":
                payload = {"data": [{"taskUUID": task["taskUUID"], "results": [{"air": "runware:100@1"}], "totalResults": 101}]}
            else:
                payload = {"data": [{"taskUUID": "wrong-task", "imageBase64Data": "wrong"},
                                    {"taskUUID": task["taskUUID"], "imageBase64Data": base64.b64encode(b"png-data").decode()}]}
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
        async def aclose(self): state["closed"] += 1
    monkeypatch.setattr(runware_images, "make_model_http_client", lambda *a: Client())
    return state


@pytest.mark.asyncio
async def test_runware_model_search_and_generation(transport, sandbox):
    found = await runware_images.discover("https://api.runware.ai/v1", "fixture-key", "FLUX")
    assert found["models"] == ["runware:100@1"] and found["has_more"]
    preset = {"base_url": "https://api.runware.ai/v1", "api_key": "fixture-key", "model": "runware:100@1",
              "extra_params": {"numberResults": 20, "positivePrompt": "overridden", "outputType": "URL"}}
    result = await runware_images.request_image("with user constraints", preset)
    assert result == b"png-data"
    task = transport["tasks"][-1]
    assert task["positivePrompt"] == "with user constraints"
    assert task["numberResults"] == 1 and task["outputType"] == "base64Data"
    assert transport["closed"] == 2


@pytest.mark.asyncio
async def test_runware_reference_input_and_sanitized_failure(transport, sandbox):
    preset = {"base_url": "https://api.runware.ai/v1", "api_key": "fixture-key", "model": "runware:100@1"}
    await runware_images.request_image("edit", preset, image_bytes=(b"ref", "image/png", "fixture"))
    assert transport["tasks"][-1]["inputs"]["seedImage"] == "data:image/png;base64,cmVm"
    transport["error"] = True
    with pytest.raises(runware_images.RunwareError) as exc:
        await runware_images.request_image("edit", preset)
    assert "fixture-key" not in str(exc.value)
    assert exc.value.error_category == "runware_insufficientCredits"
    assert transport["closed"] == 2
