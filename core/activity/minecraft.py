"""Minecraft domain orchestration. Startup owns its supervisor; routers never spawn it."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass

from core.activity import store, transcript
from core.activity.minecraft_bridge import Bridge, BridgeError, HttpBridge
from core.activity.minecraft_settings import bridge_token, readiness, settings
from core.config_loader import get_config
from core.dream.dream_state import DreamGuardStatus, get_reality_guard_status


class MinecraftError(RuntimeError):
    pass


@dataclass
class Binding:
    uid: str
    char_id: str
    session_id: str
    epoch: str
    config_digest: str
    event_seq: int = 0
    revision: int = 0
    last_model_at: float = 0
    model_calls: int = 0


class MinecraftService:
    def __init__(self, *, config=get_config, principal=None, bridge_factory=None, planner=None):
        self.config = config
        self.principal = principal or self._principal
        self.bridge_factory = bridge_factory or (lambda cfg: HttpBridge(cfg.bridge_url, bridge_token()))
        self.planner = planner
        self.binding: Binding | None = None
        self.bridge: Bridge | None = None
        self.lock = asyncio.Lock()
        self.snapshot: dict = {}
        self.last_error: str | None = None
        self.worker_alive = False
        self.chat_task: asyncio.Task | None = None
        self.model_busy = False
        self.dropped_messages = 0

    def _principal(self) -> tuple[str, str]:
        from admin.routers._common import active_char_id
        cfg = self.config()
        uid = str(cfg.get("scheduler", {}).get("owner_id") or cfg.get("default_user_id") or "owner")
        if get_reality_guard_status(uid) != DreamGuardStatus.ALLOW:
            raise MinecraftError("reality_unavailable")
        return uid, active_char_id()

    @staticmethod
    def digest(cfg) -> str:
        # Include feature grants and budgets: changes revoke previous authority.
        return hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()

    def valid(self, b: Binding) -> bool:
        try:
            cfg = settings(self.config())
            return cfg.enabled and self.digest(cfg) == b.config_digest and self.principal() == (b.uid, b.char_id)
        except Exception:
            return False

    def observation(self) -> dict:
        b = self.binding
        return {"worker_alive": self.worker_alive, "active": b is not None,
                "connection_state": self.snapshot.get("status", "unavailable"),
                "snapshot_age_seconds": max(0, time.time() - self.snapshot.get("observed_at", 0) / 1000) if self.snapshot.get("observed_at") else None,
                "current": self.snapshot.get("current"), "receipts": self.snapshot.get("receipts", [])[-30:],
                "error": self.last_error, "model_calls": b.model_calls if b else 0,
                "dropped_messages": self.dropped_messages}

    async def start(self) -> dict:
        async with self.lock:
            if not self.worker_alive:
                raise MinecraftError("worker_offline")
            cfg = settings(self.config())
            ready = readiness(cfg)
            if ready != "ready":
                raise MinecraftError(ready)
            uid, char_id = self.principal()
            if self.binding:
                raise MinecraftError("session_already_active")
            bridge = self.bridge_factory(cfg)
            session = store.create_session(uid, char_id, "minecraft", {"connection": "connecting", "model_calls": 0})
            try:
                snapshot = await bridge.request("POST", "/v1/connect", {
                    "session_id": session.session_id, "host": cfg.host, "port": cfg.port,
                    "version": cfg.version, "username": cfg.username, "auth": cfg.auth,
                    "owner_uuid": cfg.owner_uuid,
                })
                if snapshot.get("protocol_version") != 1 or snapshot.get("session_id") != session.session_id or not isinstance(snapshot.get("connection_epoch"), str):
                    raise BridgeError("invalid_bridge_response")
                binding = Binding(uid, char_id, session.session_id, snapshot["connection_epoch"], self.digest(cfg))
                if not self.valid(binding):
                    await bridge.request("POST", "/v1/disconnect", {"session_id": session.session_id, "connection_epoch": binding.epoch})
                    raise MinecraftError("authority_changed")
                session.state = {"connection": snapshot["status"], "connection_epoch": binding.epoch, "model_calls": 0}
                store.save_session(session)
            except Exception:
                store.close_session(char_id, uid, "minecraft", session.session_id)
                raise
            self.binding, self.bridge, self.snapshot = binding, bridge, snapshot
            self.last_error = None
            return {"session_id": session.session_id, "state": snapshot}

    async def _close(self, reason: str):
        b, bridge = self.binding, self.bridge
        self.binding = None; self.bridge = None
        if self.chat_task:
            self.chat_task.cancel()
        if b:
            self.last_error = reason
            try:
                if bridge:
                    await bridge.request("POST", "/v1/disconnect", {"session_id": b.session_id, "connection_epoch": b.epoch})
            except BridgeError:
                pass  # Body lease expires independently; never claim a successful disconnect.
            finally:
                store.close_session(b.char_id, b.uid, "minecraft", b.session_id)
        self.snapshot = {}

    async def close(self, reason: str = "owner_closed"):
        async with self.lock:
            await self._close(reason)

    async def command(self, action: str, params: dict | None = None, *, command_id: str | None = None,
                      expected: Binding | None = None, revision: int | None = None) -> dict:
        async with self.lock:
            b = self.binding
            if not b or not self.bridge:
                raise MinecraftError("no_active_session")
            if expected is not None and (b is not expected or b.revision != revision):
                raise MinecraftError("stale_plan")
            if action != "stop" and not self.valid(b):
                await self._close("authority_changed")
                raise MinecraftError("authority_changed")
            cfg = settings(self.config())
            params = params or {}
            if action not in {"follow", "stop", "return", "pickup", "defend", "say"}:
                raise MinecraftError("invalid_action")
            if action == "pickup" and not cfg.allow_pickup or action == "defend" and not cfg.allow_defend:
                raise MinecraftError("capability_disabled")
            if action == "pickup":
                if set(params) != {"entity_id"} or type(params["entity_id"]) is not int:
                    raise MinecraftError("invalid_params")
            elif action == "say":
                if set(params) != {"text"} or not isinstance(params["text"], str):
                    raise MinecraftError("invalid_params")
            elif params:
                raise MinecraftError("invalid_params")
            if expected is None:
                b.revision += 1  # Manual actions invalidate an in-flight model plan.
            result = await self.bridge.request("POST", "/v1/commands", {
                "session_id": b.session_id, "connection_epoch": b.epoch,
                "command_id": command_id or uuid.uuid4().hex, "action": action,
                "params": params, "expires_at": int(time.time() * 1000) + 120000,
            })
            return result

    async def tick(self):
        async with self.lock:
            b, bridge = self.binding, self.bridge
            if not b or not bridge:
                return
            session = store.find_active_session(b.char_id, b.uid, "minecraft")
            if not self.valid(b) or not session or session.session_id != b.session_id:
                await self._close("authority_changed")
                return
            snapshot = await bridge.request("POST", "/v1/heartbeat", {"session_id": b.session_id, "connection_epoch": b.epoch})
            if snapshot.get("connection_epoch") != b.epoch or snapshot.get("session_id") != b.session_id:
                await self._close("stale_binding")
                return
            self.snapshot = snapshot
            # Heartbeat itself keeps the activity alive without persisting all game ticks.
            if time.time() - float(session.state.get("saved_at", 0)) > 30:
                session.state = {"connection": snapshot.get("status"), "connection_epoch": b.epoch,
                                 "model_calls": b.model_calls, "saved_at": time.time()}
                session.updated_at = store.now_iso(); store.save_session(session)
            events = await bridge.request("GET", f"/v1/events?after={b.event_seq}")
            if events.get("connection_epoch") != b.epoch:
                raise BridgeError("stale_event_epoch")
            for event in events.get("events", [])[:20]:
                if type(event.get("seq")) is not int or event["seq"] <= b.event_seq:
                    continue
                b.event_seq = event["seq"]
                text = event.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
                # Backend starts one bounded activity-only job; there is no general pipeline reentry.
                if self.chat_task and not self.chat_task.done():
                    if text.strip() in {"停下", "停止", "!pk stop"}:
                        b.revision += 1; self.chat_task.cancel()
                    else:
                        self.dropped_messages += 1
                        continue
                self.chat_task = asyncio.create_task(self.chat(text[:500], expected=b))

    async def chat(self, text: str, *, expected: Binding | None = None) -> dict:
        # Stop is intentionally outside the model gate, including from HTTP.
        if text.strip() in {"停下", "停止", "!pk stop"}:
            return {"reply": "指令已提交。", "receipt": await self.command("stop")}
        if self.model_busy:
            raise MinecraftError("model_busy")
        self.model_busy = True
        try:
            return await self._chat(text, expected=expected)
        finally:
            self.model_busy = False

    async def _chat(self, text: str, *, expected: Binding | None = None) -> dict:
        b = expected or self.binding
        if not b or b is not self.binding or not self.valid(b):
            raise MinecraftError("no_active_session")
        # Fixed controls also work with models disabled or exhausted.
        controls = {"!pk follow": "follow", "!pk return": "return", "!pk stop": "stop", "停下": "stop", "停止": "stop"}
        if text.strip() in controls:
            receipt = await self.command(controls[text.strip()])
            return {"reply": "指令已提交。", "receipt": receipt}
        cfg = settings(self.config())
        if not cfg.model_enabled or b.model_calls >= cfg.model_calls_per_session:
            raise MinecraftError("model_budget_unavailable")
        if self.snapshot.get("status") != "connected" or time.time() * 1000 - self.snapshot.get("observed_at", 0) > 8000:
            raise MinecraftError("snapshot_not_ready")
        if time.monotonic() - b.last_model_at < cfg.model_cooldown_seconds:
            raise MinecraftError("model_cooldown")
        b.last_model_at = time.monotonic(); b.model_calls += 1
        rev = b.revision
        if self.planner is None:
            from core.activity.minecraft_companion import plan_reply
            planner = plan_reply
        else:
            planner = self.planner
        try:
            plan = await planner(b.uid, b.char_id, b.session_id, text, self.snapshot)
            if b is not self.binding or not self.valid(b) or rev != b.revision:
                raise MinecraftError("stale_plan")
            receipt = None
            if plan.action != "none":
                receipt = await self.command(plan.action, plan.params, expected=b, revision=rev)
                if receipt.get("status") in {"failed", "canceled", "outcome_unknown"}:
                    raise MinecraftError("action_" + receipt["status"])
            sent = await self.command("say", {"text": plan.reply}, expected=b, revision=rev)
            for kind, content in (("user_chat", text), ("assistant_chat", plan.reply)):
                transcript.append_entry(b.char_id, b.uid, "minecraft", b.session_id,
                                        {"type": kind, "text": content, "ts": store.now_iso()})
            return {"reply": plan.reply, "receipt": receipt, "chat_receipt": sent}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc) if isinstance(exc, (MinecraftError, BridgeError)) else "model_failed"
            raise MinecraftError(self.last_error) from None

    async def run(self):
        self.worker_alive = True
        try:
            while True:
                try:
                    await self.tick()
                    if self.chat_task and self.chat_task.done():
                        if not self.chat_task.cancelled():
                            self.chat_task.exception()  # All errors are recorded, no unhandled task logs.
                        self.chat_task = None
                except Exception:
                    async with self.lock:
                        await self._close("bridge_or_storage_failure")
                await asyncio.sleep(2)
        finally:
            self.worker_alive = False
            await self.close("backend_shutdown")


_service: MinecraftService | None = None


def install_service() -> MinecraftService:
    global _service
    _service = MinecraftService()
    return _service


def get_service() -> MinecraftService:
    if _service is None:
        raise MinecraftError("worker_offline")
    return _service
