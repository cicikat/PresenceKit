"""Content-free, actionable diagnostics for an administrator's model probe."""
import asyncio
from urllib.parse import urlsplit

import httpx

from core.llm_protocol import UpstreamResponseFormatError


def diagnose(exc: Exception) -> dict:
    status = getattr(exc, 'status_code', None) or getattr(exc, 'http_status', None)
    if not isinstance(status, int):
        status = None
    chain = []
    current = exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    kinds = ' '.join(type(item).__name__.lower() for item in chain)
    details = ' '.join(str(item).lower() for item in chain)
    if status in (400, 402, 403, 429) and any(value in details for value in ('insufficient_balance', 'insufficient_quota', '余额不足', 'quota exceeded')):
        category, message, hint = 'quota', '中转站账户余额或额度不足。', '检查中转账户余额、套餐和该模型额度；更换协议不会解决额度问题。'
    elif status in (401, 403):
        category, message, hint = 'authentication', '中转站拒绝了身份或访问权限。', '检查 API 密钥、余额/模型权限，以及 Bearer 或 x-api-key 鉴权方式；403 也可能来自网关访问限制。'
    elif status == 429:
        category, message, hint = 'rate_limit', '中转站限流或额度不足。', '稍后重试，并检查账户额度、并发和速率限制。'
    elif status in (404, 405, 501):
        category, message, hint = 'endpoint_or_model', '中转站没有提供这个接口或模型。', '核对 API 协议、模型名称和地址路径；根地址可能需要 /v1，模型目录可用不代表 Responses 接口可用。'
    elif status is not None and status >= 500:
        category, message, hint = 'upstream', '中转站或其上游服务发生错误。', '稍后重试，或切换中转；这不是设备连接协议错误。'
    elif status is not None and status >= 400:
        category, message, hint = 'request_rejected', '中转站拒绝了请求参数。', '核对所选协议、模型和 token 上限；向中转商确认是否只支持流式请求。'
    elif any(isinstance(item, (asyncio.TimeoutError, httpx.TimeoutException)) for item in chain) or 'timeout' in kinds:
        category, message, hint = 'timeout', '等待模型响应超时。', '检查网络/代理和中转负载；测试总预算为 30 秒，推理模型也可能需要更长时间。'
    elif 'ssl' in details or 'certificate' in details:
        category, message, hint = 'tls', 'HTTPS 证书或 TLS 连接失败。', '检查系统时间、证书链与代理，不要通过关闭证书校验解决。'
    elif 'connect' in kinds or 'connection refused' in details or any(isinstance(item, httpx.NetworkError) for item in chain):
        category, message, hint = 'network', '无法连接模型服务（connection refused / 网络连接失败）。', '检查地址、DNS、服务是否运行，以及后端的代理配置。'
    elif isinstance(exc, UpstreamResponseFormatError):
        category, message, hint = 'response_format', '服务有返回，但不符合所选 API 协议。', '核对 Chat Completions / Responses / Anthropic Messages；也可能是空输出、截断或中转响应字段不完整。'
    else:
        category, message, hint = 'unknown', '模型测试未能完成。', '查看错误类型与后端调用记录；请携带协议、模型和状态码排查，不要发送密钥。'
    return {'category': category, 'error': message, 'hint': hint,
            'http_status': status, 'error_type': type(exc).__name__}


def request_metadata(client) -> dict:
    protocol = getattr(client, 'api_protocol', 'chat_completions')
    if not isinstance(protocol, str):
        protocol = 'chat_completions'
    base = getattr(client, 'base_url', '')
    path = urlsplit(base).path.rstrip('/') if isinstance(base, str) else ''
    suffix = {'chat_completions': '/chat/completions', 'responses': '/responses', 'anthropic_messages': '/messages'}.get(protocol, '')
    if protocol == 'anthropic_messages' and not path:
        path = '/v1'
    return {'api_protocol': protocol, 'request_path': path if path.endswith(suffix) else path + suffix}
