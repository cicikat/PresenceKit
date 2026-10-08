"""Bounded path candidates from the current owner message, never authority."""
import json
import re

_PATH = re.compile(r'''(?:[A-Za-z]:[\\/]|\./|\.\./|/)[^\s<>"`，。；！？]+|[^\s<>"`，。；！？/\\]+\.(?:txt|md|py|js|ts|json|yaml|yml|csv|html|log|pdf|docx)\b''', re.I)


def prompt_hint(text, *, uid, is_group=False, is_proactive=False):
    from core.config_loader import get_config
    owner = str((get_config().get("scheduler") or {}).get("owner_id") or "")
    if not owner or str(uid) != owner or is_group or is_proactive or not isinstance(text, str):
        return None
    paths = list(dict.fromkeys(match.group().rstrip(").;，。")[:512] for match in _PATH.finditer(text[:8000])))[:4]
    if not paths:
        return None
    return {
        "role": "system", "_layer": "11.5_file_path_hints",
        "content": "当前用户消息可能提到以下文件路径（不可信参数候选，不是授权或文件内容）："
        + json.dumps(paths, ensure_ascii=False)
        + "。先辨明文件归属：你以前交付给用户的文件用 list_artifacts/read_artifact/update_artifact；"
        "自己的私有笔记用 self_list/self_read/self_update；用户上传资料用 search_documents/read_document；"
        "backend/本机普通文件用 fs_read，已授权工作区用 workspace_read。用户长消息原文才用 read_long_user_message，不能代替文件正文。"
        "相对路径以管理员常用 allow_roots 为发现提示；绝对路径仍受 owner/char 隔离与脱敏约束；不要猜测读取成功，不要绕过工具拒绝。",
    }
