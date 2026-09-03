"""Central deployment-mode and local-capability policy.

The backend may run on the user's machine or on a remote server.  This module
is the single policy seam for capabilities that are meaningful only on the
user's local OS.  Callers must use the logical capability names here instead
of scattering ``remote_server`` checks through tool implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LOCAL_MODE = "local"
REMOTE_SERVER_MODE = "remote_server"
KNOWN_MODES = frozenset({LOCAL_MODE, REMOTE_SERVER_MODE})

# Server-local capabilities are never allowed to operate against the server
# while the process is declared remote.  The tool schema and execute() both
# consult this set, so a hidden or hallucinated call is fail-closed as well.
REMOTE_BLOCKED_TOOLS = frozenset({
    "device_shutdown",
    "device_sleep",
    "exit_yandere",
    "fs_list",
    "fs_read",
    "workspace_list",
    "workspace_read",
    "workspace_write",
    "workspace_create",
    "workspace_update",
    "workspace_delete",
    "workspace_undo",
})

REMOTE_CLIENT_ACTIONS = frozenset({
    "desktop_minimize",
    "desktop_open_url",
    "desktop_play_pause",
    "desktop_notify",
    "play_song",
    "toy_invite",
    "dream_invite",
})


@dataclass(frozen=True)
class CapabilityDecision:
    logical_name: str
    status: str
    reason: str
    last_ack_at: float | None = None


def deployment_mode(config: dict[str, Any] | None = None) -> str:
    """Return the configured mode, defaulting safely to local for compatibility.

    Only the local config controls this value.  No request, prompt, or model
    output is accepted as an override.
    """
    if config is None:
        from core.config_loader import get_config

        config = get_config()
    block = config.get("deployment") if isinstance(config, dict) else None
    value = block.get("mode") if isinstance(block, dict) else None
    return value if value in KNOWN_MODES else LOCAL_MODE


def is_remote_server(config: dict[str, Any] | None = None) -> bool:
    return deployment_mode(config) == REMOTE_SERVER_MODE


def tool_allowed(tool_name: str, config: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    if is_remote_server(config) and tool_name in REMOTE_BLOCKED_TOOLS:
        return False, "disabled_remote_server_local_capability"
    return True, None


def capability_projection(*, desktop_ws_online: bool = False, last_ack_at: float | None = None) -> list[CapabilityDecision]:
    """Return a redacted, logical capability projection for observability."""
    remote = is_remote_server()
    decisions: list[CapabilityDecision] = []
    workspace_enabled = False
    try:
        from core.agent_runtime.workspace import capability_snapshot
        workspace_enabled = bool(capability_snapshot().get("enabled"))
    except Exception:
        pass
    decisions.append(CapabilityDecision(
        logical_name="workspace",
        status="disabled" if remote or not workspace_enabled else "enabled",
        reason=("server-local operation is disabled in remote_server mode" if remote
                else ("workspace capability is disabled" if not workspace_enabled else "explicit workspace roots")),
    ))
    for name in sorted(REMOTE_BLOCKED_TOOLS):
        decisions.append(CapabilityDecision(
            logical_name=name,
            status="disabled" if remote else "enabled",
            reason=("server-local operation is disabled in remote_server mode"
                    if remote else "local deployment"),
        ))
    for name in sorted(REMOTE_CLIENT_ACTIONS):
        decisions.append(CapabilityDecision(
            logical_name=name,
            status="online_required" if remote and not desktop_ws_online else "enabled",
            reason=("desktop WebSocket ack required in remote_server mode"
                    if remote else "desktop client action"),
            last_ack_at=last_ack_at,
        ))
    try:
        from core.tool_dispatcher import intiface_opted_in

        intiface_open = bool(intiface_opted_in())
    except Exception:
        intiface_open = False
    decisions.append(CapabilityDecision(
        logical_name="intiface_buttplug",
        status="frozen",
        reason=("frozen by Brief 171; existing opt-in is closed"
                if not intiface_open
                else "frozen by Brief 171; existing opt-in requires separate review"),
    ))
    return decisions


def desktop_action_delivery(*, online: bool, acked: bool, error: str | None = None) -> CapabilityDecision:
    if acked:
        return CapabilityDecision("desktop_action_delivery", "enabled", "ack_ok")
    return CapabilityDecision(
        "desktop_action_delivery",
        "online_required" if not online else "disabled",
        "client_offline" if not online else (error or "ack_failed"),
    )
