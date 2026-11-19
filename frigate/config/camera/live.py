from pydantic import Field

from ..base import FrigateBaseModel

__all__ = ["CameraLiveConfig"]


class CameraLiveConfig(FrigateBaseModel):
    push_to_talk: bool = Field(
        default=False,
        title="Hold to talk",
        description="Transmit microphone audio only while the live talk button is held.",
    )
    streams: dict[str, str] = Field(
        default_factory=list,
        title="Live stream names",
        description="Mapping of configured stream names to restream/go2rtc names used for live playback.",
    )
    height: int = Field(
        default=720,
        title="Live height",
        description="Height (pixels) to render the jsmpeg live stream in the Web UI; must be <= detect stream height.",
    )
    quality: int = Field(
        default=8,
        ge=1,
        le=31,
        title="Live quality",
        description="Encoding quality for the jsmpeg stream (1 highest, 31 lowest).",
    )
