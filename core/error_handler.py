"""
错误处理模块
提供重试装饰器和统一的日志记录功能
"""

import asyncio
import functools
import logging
import random
import traceback
from datetime import datetime
from pathlib import Path

# ─── 工具失败随机回复（角色风格，克制温柔）────────────────────────────────────
TOOL_FAIL_RESPONSES = [
    "（放下手中的事）这个……好像出了点问题",
    "（微微蹙眉）似乎连接不上，稍等一下",
    "（轻声）不太顺利，你先说说别的？",
    "（沉默片刻）这边出了些状况……",
    "（翻了翻）找不到结果，可能是网络的问题",
    "（搁下笔）嗯……这次没成功，抱歉",
    "（低头看了一眼）数据好像取不到……",
    "（漫不经心地）失败了，不过没关系",
    "（停顿）这里卡住了，我也不太清楚为什么",
    "（轻叹）好像出了点小差错，你不介意吧",
]


def get_tool_fail_response() -> str:
    """随机返回一条角色风格的工具失败回复"""
    return random.choice(TOOL_FAIL_RESPONSES)

def _write_error_log(module_name: str, error: Exception):
    """把错误信息写入 error.log，格式：时间戳 + 模块名 + 错误内容

    落盘经 core.error_log：脱敏、按 UTC 日轮转、保留期/单日/总量上限与
    runtime_warnings 台账同值；本文件不是全量 ERROR 视图，见 core/error_log.py 顶部说明。
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_text = traceback.format_exc()
        line = f"[{timestamp}] [{module_name}] {type(error).__name__}: {error}\n{error_text}\n"
        from core import error_log
        error_log.append(line)
    except Exception:
        # 日志本身写失败，只能打印到控制台，不再抛出
        logging.error(f"无法写入错误日志文件: {traceback.format_exc()}")


def with_retry(module_name: str = "unknown", fallback: str | None = None):
    """
    重试装饰器工厂函数

    用法：
        @with_retry(module_name="llm_client")
        async def call_llm(...):
            ...

    - 自动读取 config 中的 max_retries 和 retry_delay_seconds
    - 全部重试失败后返回 config.fallback_message（如果有）或重新抛出异常
    - 每次失败都写入 error.log
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 延迟导入避免循环依赖
            from core.config_loader import get_config
            cfg = get_config()
            max_retries = cfg.get("error", {}).get("max_retries", 3)
            retry_delay = cfg.get("error", {}).get("retry_delay_seconds", 2)
            fallback_msg = fallback or cfg.get("error", {}).get(
                "fallback_message", "我现在有点累，等会儿再聊～"
            )

            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    _write_error_log(module_name, e)
                    logging.warning(
                        f"[{module_name}] 第 {attempt}/{max_retries} 次调用失败: {e}"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)

            # 全部重试耗尽
            logging.error(f"[{module_name}] 全部 {max_retries} 次重试均失败，返回 fallback")
            return fallback_msg

        return wrapper
    return decorator


def log_error(module_name: str, error: Exception):
    """供其他模块直接调用：只记录日志，不重试"""
    _write_error_log(module_name, error)
    logging.error(f"[{module_name}] {type(error).__name__}: {error}")
