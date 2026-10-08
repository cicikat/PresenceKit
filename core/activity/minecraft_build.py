"""Shared finite hut parameter contract; no free-form block commands."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class BuildParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    material: Literal["oak_planks", "spruce_planks", "birch_planks", "cobblestone", "stone_bricks"]
    x: int = Field(ge=-30000000, le=30000000)
    y: int = Field(ge=-60, le=316)
    z: int = Field(ge=-30000000, le=30000000)
