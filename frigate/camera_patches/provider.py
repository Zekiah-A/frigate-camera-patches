"""Contracts for trusted camera patch providers."""

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class PatchControlDuration(BaseModel):
    """Allowed duration in whole seconds."""

    minimum: int = Field(gt=0)
    maximum: int = Field(gt=0)
    default: int = Field(gt=0)

    @model_validator(mode="after")
    def check_bounds(self) -> "PatchControlDuration":
        """Keep the default within the advertised bounds."""
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("Duration default must be between minimum and maximum")
        return self


class PatchControlOption(BaseModel):
    """A select control option."""

    label: str
    value: str


class PatchControl(BaseModel):
    """A control available to users with camera access."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    kind: Literal["toggle", "select", "button"]
    options: list[PatchControlOption] = Field(default_factory=list)
    duration: PatchControlDuration | None = None
    duration_values: list[str] = Field(default_factory=list)
    state_key: str | None = None
    readback: Literal["device", "ack", "none"] = "none"
    requires_admin: Literal[True] = True


class PatchCommand(BaseModel):
    """A value and optional duration for a control."""

    model_config = ConfigDict(extra="forbid", strict=True)

    value: bool | str | None
    duration: int | None = None


class PatchCommandResult(BaseModel):
    """Acknowledgment that the provider completed the command."""

    success: Literal[True] = True


class PatchError(Exception):
    """A provider failure with a message safe to send to the client."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class CameraPatch(ABC):
    """Per-camera instance, with async I/O and no hardware writes on startup."""

    controls: tuple[PatchControl, ...] = ()

    def __init__(self, camera_name: str, settings: dict[str, JsonValue]) -> None:
        """Initialize without opening a device connection."""
        self.camera_name = camera_name

    @classmethod
    def validate_settings(cls, settings: dict[str, JsonValue]) -> None:
        """Validate settings without network access or resolving secrets."""

    @abstractmethod
    async def state(self) -> dict[str, JsonValue]:
        """Read the device state, returning keys matching device-read controls."""

    @abstractmethod
    async def command(
        self, control: str, value: JsonValue, duration: int | None = None
    ) -> None:
        """Run a validated command and await its acknowledgment."""

    async def close(self) -> None:
        """Release clients, tasks, and other resources."""
