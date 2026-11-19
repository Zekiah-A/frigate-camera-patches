"""Own provider instances and serialize camera control operations."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, JsonValue

from .provider import CameraPatch, PatchCommand, PatchControl, PatchError
from .registry import PROVIDERS

if TYPE_CHECKING:
    from frigate.config import CameraConfig, FrigateConfig
    from frigate.config.camera.patches import CameraPatchConfig


logger = logging.getLogger(__name__)


class PatchStatus(BaseModel):
    """Capabilities and device state for one configured provider."""

    id: str
    controls: list[PatchControl]
    state: dict[str, JsonValue]
    available: bool
    error: Literal["unavailable", "state_failed"] | None = None


class CameraPatchesStatus(BaseModel):
    """Camera-scoped list of configured providers."""

    patches: list[PatchStatus]


@dataclass
class ActivePatch:
    """An instance and a snapshot of the config it was built from."""

    config: "CameraPatchConfig"
    provider: CameraPatch


class CameraPatchRuntime:
    """Manage camera patches on the FastAPI event loop."""

    def __init__(self, get_config: Callable[[], "FrigateConfig"]) -> None:
        self._get_config = get_config
        self._locks: dict[str, asyncio.Lock] = {}
        self._instances: dict[str, dict[str, ActivePatch]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._needs_refresh = False

    def start(self) -> None:
        """Bind runtime updates to the same loop as camera requests."""
        self._loop = asyncio.get_running_loop()

    def config_changed(self) -> None:
        """Schedule disposal of removed or changed instances on the API loop."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._schedule_refresh)

    def _schedule_refresh(self) -> None:
        if self._loop is None:
            return
        self._needs_refresh = True
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        while self._needs_refresh:
            self._needs_refresh = False
            await asyncio.gather(
                *(self._refresh_camera(name) for name in list(self._instances))
            )

    async def _refresh_camera(self, name: str) -> None:
        async with self._locks[name]:
            await self._remove_obsolete(name, self._get_config().cameras.get(name))

    async def _close_provider(self, name: str, active: ActivePatch) -> None:
        try:
            async with asyncio.timeout(10):
                await active.provider.close()
        except Exception:  # noqa: BLE001  (isolate provider faults)
            logger.warning(
                "Unable to close camera patch %s for %s", active.config.id, name
            )

    async def _remove_obsolete(
        self, name: str, camera: "CameraConfig | None"
    ) -> dict[str, ActivePatch]:
        active = self._instances.get(name, {})
        configured = (
            {patch.id: patch for patch in camera.patches}
            if camera is not None and camera.enabled
            else {}
        )
        obsolete = [
            key for key, item in active.items() if item.config != configured.get(key)
        ]
        await asyncio.gather(
            *(self._close_provider(name, active.pop(key)) for key in obsolete)
        )
        if not active:
            self._instances.pop(name, None)
        return active

    def _instance(
        self, name: str, spec: "CameraPatchConfig", active: dict[str, ActivePatch]
    ) -> CameraPatch:
        instance = active.get(spec.id)
        if instance is not None:
            return instance.provider
        try:
            provider = PROVIDERS[spec.id](name, spec.model_copy(deep=True).settings)
        except Exception:  # noqa: BLE001  (isolate provider faults)
            logger.warning("Unable to initialize camera patch %s for %s", spec.id, name)
            raise PatchError(503, "Camera patch unavailable") from None
        active[spec.id] = ActivePatch(spec.model_copy(deep=True), provider)
        self._instances[name] = active
        return provider

    async def status(self, name: str) -> CameraPatchesStatus:
        """Return safe descriptors and the latest readback from the camera."""
        async with self._locks.setdefault(name, asyncio.Lock()):
            camera = self._get_config().cameras.get(name)
            active = await self._remove_obsolete(name, camera)
            if camera is None:
                raise PatchError(404, "Camera not found")
            if not camera.enabled:
                return CameraPatchesStatus(patches=[])
            patches = []
            for spec in camera.patches:
                try:
                    provider = self._instance(name, spec, active)
                except PatchError:
                    patches.append(
                        PatchStatus(
                            id=spec.id,
                            controls=[],
                            state={},
                            available=False,
                            error="unavailable",
                        )
                    )
                    continue
                controls = list(provider.controls)
                try:
                    keys = {
                        control.state_key or control.id
                        for control in controls
                        if control.readback == "device" or control.state_key is not None
                    }
                    async with asyncio.timeout(10):
                        readback = await provider.state() if keys else {}
                    state = {
                        key: value for key, value in readback.items() if key in keys
                    }
                except Exception:  # noqa: BLE001  (isolate provider faults)
                    logger.warning(
                        "Unable to read camera patch %s for %s", spec.id, name
                    )
                    patches.append(
                        PatchStatus(
                            id=spec.id,
                            controls=controls,
                            state={},
                            available=True,
                            error="state_failed",
                        )
                    )
                    continue
                patches.append(
                    PatchStatus(
                        id=spec.id, controls=controls, state=state, available=True
                    )
                )
            return CameraPatchesStatus(patches=patches)

    async def command(
        self, name: str, patch_id: str, control_id: str, body: PatchCommand
    ) -> None:
        """Validate and dispatch a control to a configured, enabled camera."""
        async with self._locks.setdefault(name, asyncio.Lock()):
            camera = self._get_config().cameras.get(name)
            active = await self._remove_obsolete(name, camera)
            if camera is None:
                raise PatchError(404, "Camera not found")
            if not camera.enabled:
                raise PatchError(409, "Camera is disabled")
            spec = next((item for item in camera.patches if item.id == patch_id), None)
            if spec is None:
                raise PatchError(404, "Camera patch not found")
            provider = self._instance(name, spec, active)
            control = next(
                (item for item in provider.controls if item.id == control_id), None
            )
            if control is None:
                raise PatchError(404, "Camera control not found")
            duration = self._validate_command(control, body)
            try:
                async with asyncio.timeout(15):
                    await provider.command(control_id, body.value, duration)
            except PatchError:
                raise
            except TimeoutError:
                raise PatchError(504, "Camera command timed out") from None
            except Exception:  # noqa: BLE001  (isolate provider faults)
                logger.warning(
                    "Camera patch command failed for %s on %s", patch_id, name
                )
                raise PatchError(502, "Camera command failed") from None

    @staticmethod
    def _validate_command(control: PatchControl, body: PatchCommand) -> int | None:
        valid = {
            "button": body.value is None,
            "toggle": isinstance(body.value, bool),
            "select": any(body.value == option.value for option in control.options),
        }
        if not valid[control.kind]:
            raise PatchError(422, "Invalid value for this camera control")

        limits = control.duration
        if limits is None or (
            control.duration_values and body.value not in control.duration_values
        ):
            if body.duration is not None:
                raise PatchError(422, "Duration is not allowed for this command")
            return None
        duration = limits.default if body.duration is None else body.duration
        if not limits.minimum <= duration <= limits.maximum:
            raise PatchError(422, "Duration is outside the allowed range")
        return duration

    async def close(self) -> None:
        """Close active providers before shutting down the API event loop."""
        self._loop = None
        if self._refresh_task is not None:
            await self._refresh_task
        await asyncio.gather(
            *(
                self._close_provider(name, active)
                for name, patches in self._instances.items()
                for active in patches.values()
            )
        )
        self._instances.clear()
