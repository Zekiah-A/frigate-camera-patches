"""Configuration for optional camera patches."""

from pydantic import Field, JsonValue, model_validator

from frigate.camera_patches.registry import PROVIDERS

from ..base import FrigateBaseModel


class CameraPatchConfig(FrigateBaseModel):
    """A bundled provider and its camera-specific settings."""

    id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        title="Provider",
        description="Identifier of a bundled camera patch provider.",
    )
    settings: dict[str, JsonValue] = Field(
        default_factory=dict,
        title="Provider Settings",
        description="Provider-specific settings. Use references for credentials.",
    )

    @model_validator(mode="after")
    def validate_provider(self) -> "CameraPatchConfig":
        """Accept only settings for providers built into this Frigate image."""
        provider = PROVIDERS.get(self.id)
        if provider is None:
            raise ValueError(f"Unknown camera patch provider: {self.id}")
        provider.validate_settings(self.settings)
        return self
