"""Wire schema shared by the Windows task service and its REST transport."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,12}$")]
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
StepNumber = Annotated[int, Field(ge=1, le=40)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Element(Strict):
    kind: Literal["element"]
    id: int = Field(ge=0, lt=64)


class Visual(Strict):
    kind: Literal["visual"]
    description: str = Field(min_length=1, max_length=256)


Target = Annotated[Element | Visual, Field(discriminator="kind")]


class Observe(Strict):
    kind: Literal["observe"]


class Pointer(Strict):
    kind: Literal["move", "double_click"]
    target: Target


class Click(Strict):
    kind: Literal["click"]
    target: Target
    button: Literal["left", "right", "middle"] = "left"


class Scroll(Strict):
    kind: Literal["scroll"]
    target: Target
    ticks: int = Field(ge=-10, le=10)


class Text(Strict):
    kind: Literal["type_text"]
    text: str = Field(min_length=1, max_length=256)


class Press(Strict):
    kind: Literal["press_key"]
    key: Key


class Hotkey(Strict):
    kind: Literal["hotkey"]
    keys: list[Key] = Field(min_length=2, max_length=4)


class Drag(Strict):
    kind: Literal["drag"]
    start: Target = Field(alias="from")
    to: Target


class Wait(Strict):
    kind: Literal["wait"]
    ms: int = Field(ge=0, le=2000)


ActionModel = TypeAdapter(Annotated[
    Observe | Pointer | Click | Scroll | Text | Press | Hotkey | Drag | Wait,
    Field(discriminator="kind"),
])


class Poll(Strict):
    device_id: Identifier
    session_id: Identifier
    char_id: str = Field(min_length=1, max_length=80)
    scope: Literal["window", "desktop"]
    state: Literal["ready", "running", "paused", "closed"]
    remaining_ms: int = Field(ge=0, le=900_000)
    focus_ready: bool
    server_epoch: Identifier | None = None
    # Omitted by legacy clients. A grant can only be made on the first poll.
    cloud_vision: bool | None = None
    stop_agent: bool = False


class Window(Strict):
    handle: int = Field(ge=0, lt=2**64)
    pid: int = Field(ge=1, lt=2**32)
    thread: int = Field(ge=1, lt=2**32)
    created: list[Annotated[int, Field(ge=0, lt=2**32)]] = Field(min_length=2, max_length=2)


class Bounds(Strict):
    x: int = Field(ge=-65536, le=65536)
    y: int = Field(ge=-65536, le=65536)
    width: int = Field(ge=1, le=65536)
    height: int = Field(ge=1, le=65536)


class Surface(Strict):
    window: Window
    bounds: Bounds
    desktop: Bounds
    dpi: int = Field(ge=1, le=4096)
    focused: int = Field(ge=0, lt=2**64)


class Node(Strict):
    id: int = Field(ge=0, lt=64)
    name: str = Field(max_length=120)
    control_type: int
    value: Annotated[str, Field(max_length=160)] | None = None
    bounds: Bounds
    focused: bool


class Observation(Strict):
    version: StepNumber
    captured_at_ms: int = Field(ge=1, lt=2**53)
    jpeg_width: int = Field(ge=1, le=1280)
    jpeg_height: int = Field(ge=1, le=1280)
    surface: Surface
    nodes: list[Node] = Field(max_length=64)


class Receipt(Strict):
    status: Literal["ok", "changed", "failed", "stale", "canceled"]
    reason: Code | None = None
    observation: Observation | None = None


class DeliveryRef(Strict):
    device_id: Identifier
    session_id: Identifier
    task_id: Identifier
    server_epoch: Identifier
    nonce: Identifier
    seq: StepNumber


class Result(DeliveryRef):
    receipt: Receipt


class VisionRequest(DeliveryRef):
    frame_version: StepNumber
    target_index: int = Field(ge=0, le=1)
