import base64

import httpx
import pytest

from core import venice_images


@pytest.mark.asyncio
async def test_native_venice_image_and_directory(monkeypatch, sandbox):
    calls = []
    class Client:
        async def get(self, url, **kw):
            assert kw["params"] == {"type": "image"}
            return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [{"id": "fixture-image", "model_spec": {"name": "Fixture"}}]})
        async def post(self, url, **kw):
            calls.append((url, kw))
            return httpx.Response(200, request=httpx.Request("POST", url), json={"images": [base64.b64encode(b"png-bytes").decode()]})
        async def aclose(self): calls.append("closed")
    monkeypatch.setattr(venice_images, "make_model_http_client", lambda *a: Client())
    root = "https://api.venice.ai/api/v1/image/generate"
    found = await venice_images.discover(root, "fixture-key")
    assert found["models"] == ["fixture-image"]
    raw = await venice_images.request_image("user constraint", {"base_url": root, "api_key": "fixture-key", "model": "fixture-image", "extra_params": {"prompt": "override", "variants": 4}})
    assert raw == b"png-bytes"
    url, request = calls[1]
    assert url == root
    assert request["json"]["prompt"] == "user constraint" and request["json"]["variants"] == 1
    assert calls.count("closed") == 2

@pytest.mark.asyncio
async def test_insufficient_venice_balance_is_actionable_and_does_not_echo_provider_body(monkeypatch, sandbox):
    class Client:
        async def post(self, url, **kw):
            return httpx.Response(402, request=httpx.Request('POST',url), json={'error':'secret=fixture-key insufficient balance'})
        async def aclose(self): pass
    monkeypatch.setattr(venice_images,'make_model_http_client',lambda *a:Client())
    with pytest.raises(venice_images.VeniceError) as exc:
        await venice_images.request_image('test',{'base_url':'https://api.venice.ai/api/v1','model':'z-image-turbo','api_key':'fixture-key'})
    assert exc.value.error_category=='insufficient_balance' and 'fixture-key' not in str(exc.value)
