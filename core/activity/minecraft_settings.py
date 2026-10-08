"""Owner-operated Minecraft settings. Tokens remain in ignored local files."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MinecraftSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    bridge_url: str = "http://127.0.0.1:3210"
    host: str = "host.docker.internal"
    port: int = Field(default=25565, ge=1, le=65535)
    version: str = ""
    username: str = ""
    auth: Literal["microsoft", "offline"] = "microsoft"
    owner_uuid: str = ""
    allow_pickup: bool = False
    allow_defend: bool = False
    allow_mining: bool = False
    model_enabled: bool = True
    model_calls_per_session: int = Field(default=120, ge=1, le=500)
    model_cooldown_seconds: int = Field(default=10, ge=5, le=120)

    @model_validator(mode="after")
    def offline_player_name(self):
        if self.auth == "offline" and self.username and (
            len(self.username) > 16 or not all(c.isascii() and (c.isalnum() or c == "_") for c in self.username)
        ):
            raise ValueError("offline username must contain 1-16 ASCII letters, digits or underscores")
        return self

    @field_validator("bridge_url")
    @classmethod
    def local_bridge(cls, value: str) -> str:
        try:
            u = urlsplit(value)
            valid = (u.scheme == "http" and u.hostname in {"127.0.0.1", "localhost", "::1"}
                     and not u.username and not u.password and not u.query and not u.fragment
                     and u.path in {"", "/"} and u.port is not None)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("bridge_url must be an explicit local HTTP endpoint")
        return value.rstrip("/")

    @field_validator("version")
    @classmethod
    def version_value(cls, value: str) -> str:
        if value and (len(value) > 20 or any(c not in "0123456789." for c in value)):
            raise ValueError("invalid Java version")
        return value

    @field_validator("host", "username")
    @classmethod
    def connection_text(cls, value: str) -> str:
        if len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError("invalid connection field")
        return value.strip()

    @field_validator("owner_uuid")
    @classmethod
    def owner_id(cls, value: str) -> str:
        from uuid import UUID
        if value:
            try:
                return str(UUID(value))
            except ValueError:
                raise ValueError("invalid owner UUID") from None
        return value


def settings(config: dict) -> MinecraftSettings:
    return MinecraftSettings.model_validate(config.get("minecraft", {}))


def bridge_token() -> str:
    path = os.environ.get("PRESENCEKIT_MINECRAFT_TOKEN_FILE", "")
    if not path:
        return ""
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
        return token if 32 <= len(token) <= 256 else ""
    except (OSError, UnicodeError):
        return ""


def readiness(cfg: MinecraftSettings) -> str:
    if not cfg.enabled:
        return "disabled"
    if not bridge_token():
        return "missing_bridge_token"
    if not all([cfg.host, cfg.version, cfg.username, cfg.owner_uuid]):
        return "connection_not_configured"
    return "ready"
