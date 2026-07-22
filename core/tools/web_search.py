"""
DuckDuckGo 网页搜索工具
使用 ddgs 库（pip install ddgs）进行搜索。
同步调用放在线程池里跑，不阻塞事件循环。

X3：搜索结果自动沉淀到 vector_store（source="web"，去重靠 url）。
"""

import asyncio
import time

from core.error_handler import log_error
from core.proxy_config import get_aiohttp_proxy


async def search(query: str, uid: str | None = None, char_id: str | None = None) -> str:
    """
    用 DuckDuckGo 搜索，返回前3条结果（标题 + 链接 + 摘要）。
    代理从 config.yaml proxy 配置自动读取。

    uid / char_id: 如果提供，搜索结果会异步写入 vector_store（source="web"），
    供后续语义召回使用。
    """
    proxy = get_aiohttp_proxy()
    started_at = time.perf_counter()

    def _sync_search() -> list[dict]:
        from ddgs import DDGS
        ddgs = DDGS(proxy=proxy, timeout=10)
        return ddgs.text(query, max_results=3)

    try:
        loop = asyncio.get_event_loop()
        results: list[dict] = await asyncio.wait_for(
            loop.run_in_executor(None, _sync_search),
            timeout=10.0,
        )
        if not results:
            from core.api_call_log import append
            append(caller="web_search", purpose="search", provider="ddgs", model="text", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=True, output_hint="0_results")
            return "没有找到相关结果"

        from core.api_call_log import append
        append(caller="web_search", purpose="search", provider="ddgs", model="text", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=True, output_hint=f"{len(results)}_results")

        lines = []
        for i, item in enumerate(results[:3], 1):
            title = item.get("title", "")
            href  = item.get("href",  "")
            body  = item.get("body",  "")
            lines.append(f"{i}. {title}\n   {href}\n   {body}")

        # X3：将结果沉淀进向量库（fail-open，不阻塞回复）
        if uid and char_id:
            import time as _time
            _ts = _time.time()
            try:
                from core.memory import vector_store as _vs
                for item in results[:3]:
                    url = item.get("href", "")
                    title = item.get("title", "")
                    body = item.get("body", "")
                    if url:
                        text = f"{title}\n{body}".strip()
                        asyncio.create_task(
                            _vs.upsert(uid, char_id, "web", url, _ts, text)
                        )
            except Exception as _ve:
                log_error("web_search.upsert", _ve)

        return "\n\n".join(lines)

    except asyncio.TimeoutError:
        from core.api_call_log import append
        append(caller="web_search", purpose="search", provider="ddgs", model="text", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint="TimeoutError")
        return "搜索超时，请稍后再试"
    except Exception as e:
        from core.api_call_log import append
        append(caller="web_search", purpose="search", provider="ddgs", model="text", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint=type(e).__name__)
        log_error("tool.web_search", e)
        return "搜索失败，请稍后再试"
