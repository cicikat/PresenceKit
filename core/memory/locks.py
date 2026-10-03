"""
全模块共享锁池。
uid_lock：per-uid asyncio.Lock，用于同 uid 的读-改-写操作。
global_lock：全局命名锁，用于跨 uid 共享文件（如 mood_state）。
注意：defaultdict 在 asyncio 单线程事件循环里安全。
"""
import asyncio
from collections import defaultdict

_uid_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_global_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def uid_lock(uid: str) -> asyncio.Lock:
    return _uid_locks[uid]


def global_lock(name: str) -> asyncio.Lock:
    return _global_locks[name]


def locked_uids() -> list[str]:
    """Return list of uids whose uid_lock is currently held."""
    return [uid for uid, lock in _uid_locks.items() if lock.locked()]


def locked_globals() -> list[str]:
    """Return list of global lock names currently held."""
    return [name for name, lock in _global_locks.items() if lock.locked()]


# hidden_state：同步 load→modify→save 路径（现实信号、衰减 tick、afterglow 等）共用。
# 用 threading.RLock：这些路径是同步函数，可能来自事件循环或线程；临界区只是毫秒级本地文件读写。
import threading as _threading

_hidden_state_locks: dict[tuple[str, str], "_threading.RLock"] = {}
_hidden_state_locks_guard = _threading.Lock()


def hidden_state_lock(char_id: str, uid: str):
    """按 (char_id, uid) 返回 hidden_state 读-改-写锁（可重入）。"""
    key = (str(char_id), str(uid))
    with _hidden_state_locks_guard:
        lock = _hidden_state_locks.get(key)
        if lock is None:
            lock = _threading.RLock()
            _hidden_state_locks[key] = lock
        return lock
