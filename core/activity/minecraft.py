"""Minecraft domain orchestration. Startup owns its supervisor; routers never spawn it."""
from __future__ import annotations

import asyncio
import hashlib
import json
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
    reaction_calls: int = 0
    last_reaction_at: float = 0
    reaction_signature: str = ""
    route_digest: str = ""
    companion_goal: str | None = None
    goal_renewed_at: float = 0


class MinecraftService:
    def __init__(self, *, config=get_config, principal=None, bridge_factory=None, planner=None, reactor=None):
        self.config = config
        self.principal = principal or self._principal
        self.bridge_factory = bridge_factory or (lambda cfg: HttpBridge(cfg.bridge_url, bridge_token()))
        self.planner = planner
        self.reactor = reactor
        self.binding: Binding | None = None
        self.bridge: Bridge | None = None
        self.lock = asyncio.Lock()
        self.snapshot: dict = {}
        self.last_error: str | None = None
        self.worker_alive = False
        self.chat_task: asyncio.Task | None = None
        self.model_busy = False
        self.dropped_messages = 0
        self.last_model_latency_seconds: float | None = None
        self.reaction_task: asyncio.Task | None = None
        self.reaction_error: str | None = None
        self.reaction_latency_seconds: float | None = None
        self.reaction_busy = False

    def route_digest(self) -> str:
        return hashlib.sha256(json.dumps(self.config().get("model_presets", {}), sort_keys=True, default=str).encode()).hexdigest()

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
            return cfg.enabled and self.digest(cfg) == b.config_digest and self.route_digest() == b.route_digest and self.principal() == (b.uid, b.char_id)
        except Exception:
            return False

    def observation(self) -> dict:
        b = self.binding
        return {"worker_alive": self.worker_alive, "active": b is not None,
                "connection_state": self.snapshot.get("status", "unavailable"),
                "snapshot_age_seconds": max(0, time.time() - self.snapshot.get("observed_at", 0) / 1000) if self.snapshot.get("observed_at") else None,
                "current": self.snapshot.get("current"), "receipts": self.snapshot.get("receipts", [])[-30:],
                "error": self.last_error, "model_calls": b.model_calls if b else 0,
                "dropped_messages": self.dropped_messages,
                "last_model_latency_seconds": self.last_model_latency_seconds,
                "reaction_calls": b.reaction_calls if b else 0, "reaction_error": self.reaction_error,
                "reaction_latency_seconds": self.reaction_latency_seconds,
                "companion_goal": b.companion_goal if b else None,
                "action_outcomes": {status: sum(r.get("status") == status for r in self.snapshot.get("receipts", []))
                                    for status in ("running", "succeeded", "failed", "canceled", "outcome_unknown")}}

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
                binding.route_digest = self.route_digest()
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
        if self.reaction_task:
            self.reaction_task.cancel()
        if b:
            self.last_error = reason
            try:
                if bridge:
                    final = await bridge.request("POST", "/v1/disconnect", {"session_id": b.session_id, "connection_epoch": b.epoch})
                    if final.get("connection_epoch") == b.epoch:
                        self.snapshot = final
            except BridgeError:
                pass  # Body lease expires independently; never claim a successful disconnect.
            finally:
                session = store.load_session(b.char_id, b.uid, "minecraft", b.session_id)
                if session:
                    session.state = {"connection": "closed", "close_reason": reason,
                                     "model_calls": b.model_calls, "last_model_latency_seconds": self.last_model_latency_seconds,
                                     "reaction_calls": b.reaction_calls, "reaction_error": self.reaction_error,
                                     "reaction_latency_seconds": self.reaction_latency_seconds,
                                     "current": self.snapshot.get("current"), "receipts": self.snapshot.get("receipts", [])[-30:]}
                    store.save_session(session)
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
            if action not in {"follow", "stop", "return", "pickup", "defend", "say", "collect_iron", "approach", "accompany", "protect", "build_house"}:
                raise MinecraftError("invalid_action")
            if action == "pickup" and not cfg.allow_pickup or action in {"defend", "protect"} and not cfg.allow_defend:
                raise MinecraftError("capability_disabled")
            if action == "build_house":
                if not cfg.allow_building:
                    raise MinecraftError("capability_disabled")
                from core.activity.minecraft_build import BuildParams
                params = BuildParams.model_validate(params).model_dump()
            elif action == "collect_iron":
                if not cfg.allow_mining:
                    raise MinecraftError("capability_disabled")
                if set(params) != {"count", "radius"} or any(type(params[k]) is not int or not 1 <= params[k] <= 8 for k in params):
                    raise MinecraftError("invalid_params")
            elif action == "pickup":
                if set(params) != {"entity_id"} or type(params["entity_id"]) is not int:
                    raise MinecraftError("invalid_params")
            elif action == "say":
                if set(params) != {"text"} or not isinstance(params["text"], str):
                    raise MinecraftError("invalid_params")
            elif params:
                raise MinecraftError("invalid_params")
            if expected is None:
                b.revision += 1  # Manual actions invalidate an in-flight model plan.
            if action != "say":
                b.companion_goal = action if action in {"accompany", "protect"} else None
                b.goal_renewed_at = time.monotonic()
            result = await self.bridge.request("POST", "/v1/commands", {
                "session_id": b.session_id, "connection_epoch": b.epoch,
                "command_id": command_id or uuid.uuid4().hex, "action": action,
                "params": params, "expires_at": int(time.time() * 1000) + 120000,
                "owner_stop_revision": self.snapshot.get("owner_stop_revision", 0),
            })
            return result

    async def react(self, b: Binding, text: str = "") -> dict | None:
        cfg = settings(self.config())
        if self.reaction_busy or not cfg.reaction_enabled or not self.valid(b) or b.reaction_calls >= cfg.reaction_calls_per_session:
            return None
        if time.monotonic() - b.last_reaction_at < cfg.reaction_cooldown_seconds:
            return None
        if self.snapshot.get("status") != "connected" or not self.snapshot.get("game"):
            return None
        current = self.snapshot.get("current") or {}
        if current.get("action") not in {None, "follow", "accompany", "protect", "approach"}:
            return None
        if not text and b.companion_goal not in {"accompany", "protect"}:
            return None
        b.reaction_calls += 1; b.last_reaction_at = time.monotonic()
        self.reaction_busy = True
        rev = b.revision; started = time.monotonic()
        try:
            from core.activity.minecraft_reaction import judge
            decision = await asyncio.wait_for((self.reactor or judge)(b.char_id, self.snapshot, text), timeout=3)
            self.reaction_latency_seconds = round(time.monotonic() - started, 3)
            if b is not self.binding or not self.valid(b) or b.revision != rev:
                raise MinecraftError("stale_reaction")
            action = decision.action
            if action == "none" or action == current.get("action"):
                return None
            # Autonomous changes cannot broaden a user-granted mode.
            if not text and (action != "stop" and action != b.companion_goal):
                return None
            if current.get("action"):
                await self.command("stop", expected=b, revision=rev)
            result = await self.command(action, expected=b, revision=rev)
            b.revision += 1
            self.reaction_error = None
            return {"action": action, "receipt": result, "revision": b.revision}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.reaction_error = str(exc) if isinstance(exc, (MinecraftError, BridgeError)) else "reaction_failed"
            return None
        finally:
            self.reaction_busy = False

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
            if snapshot.get("owner_stop_revision", 0) != self.snapshot.get("owner_stop_revision", 0):
                b.revision += 1
                b.companion_goal = None
            self.snapshot = snapshot
            current = snapshot.get("current") or {}
            if b.companion_goal and current.get("action") == b.companion_goal and time.monotonic() - b.goal_renewed_at > 90:
                await bridge.request("POST", "/v1/commands", {"session_id": b.session_id, "connection_epoch": b.epoch,
                    "command_id": uuid.uuid4().hex, "action": "stop", "params": {}, "expires_at": int(time.time()*1000)+120000})
                receipt = await bridge.request("POST", "/v1/commands", {"session_id": b.session_id, "connection_epoch": b.epoch,
                    "command_id": uuid.uuid4().hex, "action": b.companion_goal, "params": {}, "expires_at": int(time.time()*1000)+120000,
                    "owner_stop_revision": snapshot.get("owner_stop_revision", 0)})
                b.goal_renewed_at = time.monotonic(); b.revision += 1
                if receipt.get("status") != "running": b.companion_goal = None
            elif b.companion_goal and not current:
                b.companion_goal = None  # Never restart a locally stopped/unsafe action.
            game = snapshot.get("game") or {}
            signature = json.dumps({"owner": game.get("owner_visible"), "health": game.get("health"),
                                    "threats": game.get("threats"), "dimension": game.get("dimension")}, sort_keys=True)
            if signature != b.reaction_signature:
                b.reaction_signature = signature
                if not self.reaction_task or self.reaction_task.done():
                    self.reaction_task = asyncio.create_task(self.react(b))
            # Heartbeat itself keeps the activity alive without persisting all game ticks.
            if time.time() - float(session.state.get("saved_at", 0)) > 30:
                session.state = {"connection": snapshot.get("status"), "connection_epoch": b.epoch,
                                 "model_calls": b.model_calls, "reaction_calls": b.reaction_calls,
                                 "reaction_error": self.reaction_error, "reaction_latency_seconds": self.reaction_latency_seconds,
                                 "saved_at": time.time(),
                                 "current": snapshot.get("current"), "receipts": snapshot.get("receipts", [])[-30:]}
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
        fast = await self.react(b, text) if not self.reaction_task or self.reaction_task.done() else None
        if fast:
            rev = fast["revision"]
            if fast["receipt"].get("status") in {"failed", "canceled", "outcome_unknown"}:
                raise MinecraftError("action_" + fast["receipt"]["status"])
        if b.revision != rev or not self.valid(b):
            raise MinecraftError("stale_plan")
        if self.planner is None:
            from core.activity.minecraft_companion import plan_reply
            planner = plan_reply
        else:
            planner = self.planner
        try:
            model_started = time.monotonic()
            plan = await planner(b.uid, b.char_id, b.session_id, text, {**self.snapshot,
                "capabilities": {k: getattr(cfg, k) for k in ("allow_building", "allow_defend", "allow_pickup", "allow_mining")},
                "accepted_fast_action": fast["action"] if fast else None})
            self.last_model_latency_seconds = round(time.monotonic() - model_started, 3)
            if b is not self.binding or not self.valid(b) or rev != b.revision:
                raise MinecraftError("stale_plan")
            receipt = fast["receipt"] if fast else None
            if plan.action != "none" and not fast:
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
