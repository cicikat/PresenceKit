"""
QQ 协议适配器
通过 WebSocket 连接 NapCat（OneBot 11 协议）
负责接收事件、发送消息、黑名单过滤
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

import aiohttp
import yaml

from core.config_loader import get_config
from core.error_handler import log_error

logger = logging.getLogger(__name__)

# 黑名单缓存（字符串列表）
_blacklist: list[str] = []
# 消息接收回调（由 main.py 通过 on_message 注入）
_message_callback: Callable | None = None
# WebSocket 连接对象
_ws: aiohttp.ClientWebSocketResponse | None = None
# 机器人自己的 QQ 号（用于判断 at 消息）
_self_id: str = ""
# 启动通知是否已发送（防止断线重连时重复发）
_startup_notify_sent: bool = False
# 等待 WS 响应的 pending futures（echo → Future）
_pending_responses: dict[str, "asyncio.Future"] = {}


def _load_blacklist():
    """加载黑名单，文件不存在时使用空列表"""
    from core.sandbox import get_paths
    global _blacklist
    try:
        bf = get_paths().blacklist()
        if bf.exists():
            with open(bf, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _blacklist = [str(uid) for uid in data.get("blacklist", [])]
        else:
            _blacklist = []
    except Exception as e:
        log_error("qq_adapter._load_blacklist", e)
        _blacklist = []
    logger.info(f"[qq_adapter] 黑名单已加载，共 {len(_blacklist)} 条")


def is_blacklisted(user_id: str) -> bool:
    """判断用户是否在黑名单中"""
    return str(user_id) in _blacklist


def on_message(callback: Callable):
    """
    注册消息接收回调

    callback 签名：async def callback(message: dict) -> None
    message 格式：
    {
        "user_id":     "123456",       # 发送者QQ号
        "group_id":    "789012",       # 群号（私聊为None）
        "content":     "消息内容",
        "sender_name": "用户昵称",
        "timestamp":   1234567890,
    }
    """
    global _message_callback
    _message_callback = callback


async def send_message(target_id: str, content: str, is_group: bool = False):
    """
    发送消息到指定目标

    参数:
        target_id: 私聊时为 user_id，群聊时为 group_id
        content:   消息文本内容
        is_group:  True=群聊，False=私聊
    """
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("qq_send")
    if _ws is None or _ws.closed:
        logger.error("[qq_adapter] WebSocket 未连接，无法发送消息")
        return

    if is_group:
        action = "send_group_msg"
        params = {"group_id": int(target_id), "message": content}
    else:
        action = "send_private_msg"
        params = {"user_id": int(target_id), "message": content}

    payload = {
        "action": action,
        "params": params,
        "echo": f"send_{int(time.time() * 1000)}",
    }

    try:
        await _ws.send_str(json.dumps(payload, ensure_ascii=False))
        logger.info(f"[qq_adapter] WS 帧已发出 -> {action} target={target_id}: {content[:50]!r}")
    except Exception as e:
        log_error("qq_adapter.send_message", e)
        logger.error(f"[qq_adapter] WS 发送失败: {type(e).__name__}: {e}")


async def send_record(target_id: str, file: str, is_group: bool = False):
    """
    发送语音消息（OneBot 11 record 消息段）。

    参数:
        target_id — 私聊时为 user_id，群聊时为 group_id
        file      — 音频来源，支持：
                    "base64://<base64数据>"  内联 base64
                    "file:///path/to/file"   本地文件路径
                    "http://..."             远程 URL
        is_group  — True=群聊，False=私聊
    """
    if _ws is None or _ws.closed:
        logger.error("[qq_adapter] WebSocket 未连接，无法发送语音")
        return

    message_seg = [{"type": "record", "data": {"file": file}}]

    if is_group:
        action = "send_group_msg"
        params = {"group_id": int(target_id), "message": message_seg}
    else:
        action = "send_private_msg"
        params = {"user_id": int(target_id), "message": message_seg}

    payload = {
        "action": action,
        "params": params,
        "echo": f"record_{int(time.time() * 1000)}",
    }

    try:
        await _ws.send_str(json.dumps(payload, ensure_ascii=False))
        logger.info(f"[qq_adapter] 语音消息帧已发出 -> {action} target={target_id}")
    except Exception as e:
        log_error("qq_adapter.send_record", e)
        logger.error(f"[qq_adapter] 语音消息发送失败: {type(e).__name__}: {e}")


async def send_image(target_id: str, file_path: str, is_group: bool = False):
    """
    发送本地图片（OneBot 11 image 消息段）
    file_path: 本地绝对路径
    """
    if _ws is None or _ws.closed:
        logger.error("[qq_adapter] WebSocket 未连接，无法发送图片")
        return

    file_uri = "file:///" + str(Path(file_path).resolve()).replace("\\", "/")
    message_seg = [{"type": "image", "data": {"file": file_uri}}]

    if is_group:
        action = "send_group_msg"
        params = {"group_id": int(target_id), "message": message_seg}
    else:
        action = "send_private_msg"
        params = {"user_id": int(target_id), "message": message_seg}

    payload = {
        "action": action,
        "params": params,
        "echo": f"image_{int(time.time() * 1000)}",
    }

    try:
        await _ws.send_str(json.dumps(payload, ensure_ascii=False))
        logger.info(f"[qq_adapter] 图片消息帧已发出 -> {action} target={target_id}: {file_path}")
    except Exception as e:
        log_error("qq_adapter.send_image", e)


def _parse_event(raw: dict) -> dict | None:
    """
    解析 OneBot 11 事件，统一格式化为内部消息结构

    只处理 message 类型事件
    群聊消息：只响应 at 机器人的消息
    黑名单用户：静默丢弃（返回 None）
    """
    # 只处理消息事件
    if raw.get("post_type") != "message":
        return None

    message_type = raw.get("message_type", "")
    user_id = str(raw.get("user_id", ""))
    group_id = str(raw.get("group_id", "")) if message_type == "group" else None

    # 黑名单检查：命中则静默丢弃
    if is_blacklisted(user_id):
        logger.debug(f"[qq_adapter] 黑名单用户 {user_id}，消息已丢弃")
        return None

    # 提取消息内容
    raw_message = raw.get("raw_message", "") or ""
    message_array = raw.get("message", [])

    # 处理消息数组格式（CQ码/消息段）
    content = _extract_text_content(raw_message, message_array)

    # 提取图片和文件信息
    image_urls = _extract_images(message_array)
    file_info = _extract_file(message_array)
    audio_url = next((str(seg.get("data", {}).get("url") or seg.get("data", {}).get("file") or "")
                      for seg in message_array if isinstance(seg, dict) and seg.get("type") == "record"), "")

    # 群聊：只响应 at 机器人的消息
    if message_type == "group":
        if not _self_id:
            logger.error("[qq_adapter] _self_id 未初始化，无法判断 @ 对象，丢弃群消息")
            return None
        # 双轨检测：消息段数组优先，CQ 串兜底（兼容 NapCat 两种下发格式）
        at_in_array = any(
            isinstance(seg, dict)
            and seg.get("type") == "at"
            and str(seg.get("data", {}).get("qq", "")) == str(_self_id)
            for seg in message_array
        )
        at_in_cq = f"[CQ:at,qq={_self_id}]" in raw_message
        if not (at_in_array or at_in_cq):
            return None
        # 去掉 at 标记（两种格式都清理）
        content = re.sub(r"\[CQ:at,[^\]]*\]", "", content).strip()
        content = content.replace(f"@{_self_id}", "").strip()

    if not content and not image_urls and not file_info and not audio_url:
        return None

    # 提取发送者信息
    sender = raw.get("sender", {})
    sender_name = (
        sender.get("card") or      # 群名片
        sender.get("nickname") or  # 昵称
        user_id
    )

    return {
        "user_id": user_id,
        "group_id": group_id,
        "content": content,
        "sender_name": sender_name,
        "timestamp": raw.get("time", int(time.time())),
        "event_id": f"qq:{raw['message_id']}" if raw.get("message_id") is not None else "",
        "image_urls": image_urls,
        "file_info": file_info,
        "audio_url": audio_url,
    }


def _extract_text_content(raw_message: str, message_array: list) -> str:
    """
    从 OneBot 消息中提取纯文本内容

    优先使用 raw_message（字符串格式）
    raw_message 为空时从 message 数组中提取文本段
    """
    if raw_message:
        return raw_message

    # 从消息段数组中提取文本
    texts = []
    for seg in message_array:
        if isinstance(seg, dict) and seg.get("type") == "text":
            texts.append(seg.get("data", {}).get("text", ""))
    return "".join(texts)


def _extract_images(message_array: list) -> list:
    """从消息段中提取所有图片URL"""
    urls = []
    for seg in message_array:
        if isinstance(seg, dict) and seg.get("type") == "image":
            data = seg.get("data", {})
            url = data.get("url") or data.get("file", "")
            if url:
                urls.append(url)
    return urls


def _extract_file(message_array: list):
    """从消息段中提取文件信息"""
    for seg in message_array:
        if isinstance(seg, dict) and seg.get("type") == "file":
            data = seg.get("data", {})
            return {
                "name": data.get("file", ""),
                "url": data.get("url", ""),
                "file_id": data.get("file_id", ""),
                "size": data.get("file_size", 0),
            }
    return None


async def connect_and_listen():
    """
    连接 NapCat WebSocket 并持续监听事件

    断线后自动重连（每5秒重试一次）
    """
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed("qq_connect")
    global _ws, _self_id

    _load_blacklist()
    cfg = get_config()["qq"]
    host = cfg["host"]
    port = cfg["port"]
    ws_url = f"ws://{host}:{port}"

    logger.info(f"[qq_adapter] 正在连接 NapCat: {ws_url}")

    while True:  # 自动重连循环
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    ws_url,
                    heartbeat=30,        # 30秒心跳，保活连接
                    timeout=aiohttp.ClientWSTimeout(ws_close=10),
                ) as ws:
                    _ws = ws
                    logger.info(f"[qq_adapter] 已成功连接到 NapCat")

                    # 获取机器人自身 QQ 号
                    await _fetch_self_id(ws)

                    # 发送启动通知（仅首次连接，重连时不重复发）
                    global _startup_notify_sent
                    if not _startup_notify_sent:
                        _startup_notify_sent = True
                        asyncio.create_task(send_startup_notify())

                    # 开始监听消息
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await _handle_raw_message(msg.data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logger.error(f"[qq_adapter] WebSocket 错误: {ws.exception()}")
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                            logger.warning("[qq_adapter] WebSocket 连接已关闭")
                            break

        except aiohttp.ClientConnectorError:
            logger.warning(f"[qq_adapter] 无法连接到 {ws_url}，5秒后重试...")
        except Exception as e:
            log_error("qq_adapter.connect_and_listen", e)
            logger.warning("[qq_adapter] 连接异常，5秒后重试...")
        finally:
            _ws = None

        await asyncio.sleep(5)  # 重连等待


async def _fetch_self_id(ws: aiohttp.ClientWebSocketResponse):
    """向 NapCat 发送 get_login_info 请求，获取机器人自身 QQ 号"""
    global _self_id
    try:
        payload = {"action": "get_login_info", "echo": "get_self_id"}
        await ws.send_str(json.dumps(payload))
        # 等待响应（简单轮询，最多等3秒）
        for _ in range(30):
            msg = await asyncio.wait_for(ws.receive(), timeout=0.1)
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("echo") == "get_self_id" and data.get("status") == "ok":
                    _self_id = str(data["data"]["user_id"])
                    logger.info(f"[qq_adapter] 机器人 QQ 号: {_self_id}")
                    return
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        log_error("qq_adapter._fetch_self_id", e)
    logger.warning("[qq_adapter] 未能获取机器人自身 QQ 号，群聊 at 过滤可能失效")


async def ws_call(action: str, params: dict | None = None, timeout: float = 5.0) -> dict | None:
    """
    向 NapCat 发送 WebSocket API 请求并等待响应。

    参数：
        action  — OneBot 11 动作名，如 "get_group_list"
        params  — 请求参数字典
        timeout — 等待响应超时秒数
    返回：
        响应 dict（含 status / data），超时或未连接返回 None
    """
    if _ws is None or _ws.closed:
        logger.warning(f"[qq_adapter.ws_call] WS 未连接，无法执行 {action}")
        return None

    echo = f"{action}_{int(time.time() * 1000)}"
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    _pending_responses[echo] = future

    payload = {"action": action, "params": params or {}, "echo": echo}
    try:
        await _ws.send_str(json.dumps(payload, ensure_ascii=False))
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[qq_adapter.ws_call] {action} 超时（{timeout}s）")
        return None
    except Exception as e:
        log_error("qq_adapter.ws_call", e)
        return None
    finally:
        _pending_responses.pop(echo, None)


async def _handle_raw_message(raw_str: str):
    """解析原始 WebSocket 消息，优先匹配 pending 请求，再分发为事件"""
    try:
        raw = json.loads(raw_str)
    except json.JSONDecodeError:
        return

    # 响应匹配：有 echo 且在等待队列中
    echo = raw.get("echo")
    if echo and echo in _pending_responses:
        fut = _pending_responses.pop(echo)
        if not fut.done():
            fut.set_result(raw)
        return  # 不再作为消息事件分发

    message = _parse_event(raw)
    if message and _message_callback:
        await _message_callback(message)


def reload_blacklist():
    """热重载黑名单（admin 修改后调用）"""
    _load_blacklist()
    logger.info("[qq_adapter] 黑名单已热重载")


async def send_startup_notify():
    """
    连接成功后发送启动通知
    读取 config.notify，若 enabled=true 则向 target_qq 发送 message
    """
    cfg = get_config().get("notify", {})
    if not cfg.get("enabled", False):
        return

    target_qq = str(cfg.get("target_qq", ""))
    message = cfg.get("message", "")
    if not target_qq or not message:
        logger.warning("[qq_adapter] notify 配置不完整，跳过启动通知")
        return

    # 稍等一下，确保 WebSocket 连接稳定
    await asyncio.sleep(1)
    await send_message(target_qq, message, is_group=False)
    logger.info(f"[qq_adapter] 启动通知已发送 -> {target_qq}: {message!r}")
