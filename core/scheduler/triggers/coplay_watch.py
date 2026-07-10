"""
coplay_watch trigger — 陪玩模式游戏检测 + 感知轮询 + 收尾链（Brief 39 + 40 + 42）。

参考 garden_water.py 的轮询写法：每次主循环 tick（core/scheduler/loop.py，固定
60 秒一次）都被调用一次，只推状态机 + 产出 moment，不发言、不走 gating/proposer
（主动开口是 Brief 41 commentator 的事，走独立的 proposer 注册）。

三段各自内部节流/幂等，互不依赖：
  - watcher.tick        — config.coplay.poll_interval 节流（游戏检测 + 拉起桌宠）。
  - observer.tick        — config.coplay.screenshot_interval 节流（截屏/差分/OCR/
    VLM/存档 watch），只在 watcher 判定当前状态是 active 时才跑。
  - session_close.run_session_close — 只在 closing 状态跑；收尾成功后自己把
    状态转回 armed，所以下一 tick 自然不会重复执行（不需要额外冷却）。
"""

import logging

from core.config_loader import get_config

logger = logging.getLogger(__name__)


async def _check_coplay_watch() -> None:
    if not get_config().get("coplay", {}).get("enabled", False):
        return

    uid = str(get_config().get("scheduler", {}).get("owner_id", "") or "")
    if not uid:
        return

    try:
        from core.pipeline_registry import get as _get_pipeline
        pl = _get_pipeline()
        char_id = (pl._active_character_id if pl else None) or "yexuan"

        from core.coplay import watcher, session
        await watcher.tick(uid, char_id=char_id)

        state = session.read_state(uid, char_id=char_id)
        if state.get("status") == session.CoplayStatus.ACTIVE.value:
            from core.coplay import observer
            await observer.tick(uid, char_id=char_id, game_id=state.get("game_id"))
        elif state.get("status") == session.CoplayStatus.CLOSING.value:
            from core.coplay import session_close
            await session_close.run_session_close(uid, char_id=char_id)
    except Exception:
        logger.exception("[coplay_watch] tick failed")
