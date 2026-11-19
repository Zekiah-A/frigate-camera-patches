"""Tests for bundled camera patch configuration, lifecycle, and access."""

import asyncio
import unittest
from typing import ClassVar
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue, ValidationError

from frigate.api.auth import require_admin_by_default
from frigate.api.camera_patches import router
from frigate.camera_patches.provider import (
    CameraPatch,
    PatchCommand,
    PatchControl,
    PatchControlDuration,
    PatchControlOption,
    PatchError,
)
from frigate.camera_patches.registry import PROVIDERS
from frigate.camera_patches.runtime import CameraPatchRuntime
from frigate.config import CameraConfig, FrigateConfig
from frigate.config.camera.patches import CameraPatchConfig


class MockPatch(CameraPatch):
    controls = (
        PatchControl(id="mirror", label="Mirror", kind="toggle", readback="device"),
        PatchControl(
            id="light",
            label="Light",
            kind="select",
            options=[
                PatchControlOption(label="On", value="on"),
                PatchControlOption(label="Off", value="off"),
            ],
            duration=PatchControlDuration(minimum=2, maximum=60, default=10),
            duration_values=["on"],
            readback="device",
        ),
        PatchControl(
            id="stop", label="Stop", kind="button", readback="ack", state_key="sound"
        ),
    )
    actions: ClassVar[list[tuple[str, JsonValue, int | None]]] = []
    closed: ClassVar[list[str]] = []

    def __init__(self, camera_name: str, settings: dict[str, JsonValue]) -> None:
        super().__init__(camera_name, settings)
        self.settings = settings
        if settings.get("connect") is False:
            raise PatchError(503, "Connection unavailable")

    @classmethod
    def validate_settings(cls, settings: dict[str, JsonValue]) -> None:
        if "password" in settings:
            raise ValueError("Specify a secret reference")

    async def state(self) -> dict[str, JsonValue]:
        if self.settings.get("read") is False:
            raise TimeoutError("a private transport detail")
        return {
            "mirror": False,
            "light": "off",
            "sound": "off",
            "stop": True,
            "private": "hidden",
        }

    async def command(
        self, control: str, value: JsonValue, duration: int | None = None
    ) -> None:
        self.actions.append((control, value, duration))

    async def close(self) -> None:
        self.closed.append(self.camera_name)


def config_with_patches(**settings: JsonValue) -> FrigateConfig:
    """Create two cameras with only the front camera enabled for patch controls."""
    camera = {
        "ffmpeg": {
            "inputs": [{"path": "rtsp://camera.local/live", "roles": ["detect"]}]
        },
        "detect": {"height": 720, "width": 1280},
    }
    return FrigateConfig(
        mqtt={"enabled": False},
        auth={"roles": {"front_viewer": ["front"], "back_viewer": ["back"]}},
        cameras={
            "front": {
                **camera,
                "patches": [{"id": "example", "settings": settings}],
            },
            "back": camera,
        },
    )


class TestCameraPatchRuntime(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.dict(PROVIDERS, {"example": MockPatch}))
        MockPatch.actions.clear()
        MockPatch.closed.clear()
        self.config = config_with_patches()

    async def asyncSetUp(self):
        self.runtime = CameraPatchRuntime(lambda: self.config)
        self.runtime.start()
        self.addAsyncCleanup(self.runtime.close)

    async def test_state_only_exposes_advertised_device_readback(self):
        result = (await self.runtime.status("front")).patches[0]
        self.assertTrue(result.available)
        self.assertEqual(
            result.state, {"mirror": False, "light": "off", "sound": "off"}
        )
        self.assertEqual((await self.runtime.status("back")).patches, [])
        self.assertEqual(MockPatch.actions, [])

    async def test_state_failure_preserves_controls_and_is_sanitized(self):
        self.config = config_with_patches(read=False)
        result = (await self.runtime.status("front")).patches[0]
        self.assertTrue(result.available)
        self.assertEqual(result.error, "state_failed")
        self.assertEqual(result.state, {})
        self.assertEqual(len(result.controls), 3)
        await self.runtime.command("front", "example", "stop", PatchCommand(value=None))
        self.assertEqual(MockPatch.actions, [("stop", None, None)])

    async def test_provider_construction_failure_is_reported(self):
        self.config = config_with_patches(connect=False)
        result = (await self.runtime.status("front")).patches[0]
        self.assertFalse(result.available)
        self.assertEqual(result.error, "unavailable")

    async def test_command_is_validated_before_dispatch(self):
        cases = [
            ("light", {"value": "unknown"}),
            ("light", {"value": "on", "duration": 61}),
            ("light", {"value": "off", "duration": 10}),
            ("mirror", {"value": "false"}),
            ("stop", {"value": False}),
        ]
        for control, parameters in cases:
            with self.subTest(control=control, parameters=parameters):
                with self.assertRaises(PatchError) as raised:
                    await self.runtime.command(
                        "front", "example", control, PatchCommand(**parameters)
                    )
                self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(MockPatch.actions, [])

        await self.runtime.command(
            "front", "example", "light", PatchCommand(value="on")
        )
        self.assertEqual(MockPatch.actions, [("light", "on", 10)])
        with self.assertRaises(ValidationError):
            PatchCommand(value="on", duration=True)

    async def test_disabled_camera_cannot_be_controlled(self):
        self.config.cameras["front"].enabled = False
        with self.assertRaises(PatchError) as raised:
            await self.runtime.command(
                "front", "example", "stop", PatchCommand(value=None)
            )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(MockPatch.actions, [])

    async def test_applied_config_disposes_removed_camera_without_next_request(self):
        closed = asyncio.Event()

        async def signal_close(provider: MockPatch) -> None:
            closed.set()

        with patch.object(MockPatch, "close", signal_close):
            await self.runtime.status("front")
            self.config.cameras.pop("front")
            await asyncio.to_thread(self.runtime.config_changed)
            await asyncio.wait_for(closed.wait(), timeout=1)
        self.assertEqual((await self.runtime.status("back")).patches, [])

    def test_configuration_requires_unique_bundled_ids(self):
        with self.assertRaisesRegex(ValidationError, "Unknown camera patch provider"):
            CameraPatchConfig(id="unknown")
        with self.assertRaisesRegex(ValidationError, "Specify a secret reference"):
            CameraPatchConfig(id="example", settings={"password": "example"})
        with self.assertRaisesRegex(ValidationError, "unique per camera"):
            CameraConfig.model_validate(
                {
                    "ffmpeg": {
                        "inputs": [
                            {"path": "rtsp://camera.local/live", "roles": ["detect"]}
                        ]
                    },
                    "patches": [{"id": "example"}, {"id": "example"}],
                }
            )


class TestCameraPatchApi(unittest.TestCase):
    def test_routes_enforce_camera_read_and_admin_write(self):
        with patch.dict(PROVIDERS, {"example": MockPatch}):
            app = FastAPI(dependencies=[Depends(require_admin_by_default())])
            app.frigate_config = config_with_patches()
            app.camera_patches = CameraPatchRuntime(lambda: app.frigate_config)
            app.include_router(router)
            MockPatch.actions.clear()
            with TestClient(app) as client:

                def credentials(role: str) -> dict[str, str]:
                    return {"remote-user": "example", "remote-role": role}

                camera_path = "/front/patches"
                command_path = camera_path + "/example/stop"
                self.assertEqual(client.get(camera_path).status_code, 401)
                self.assertEqual(
                    client.get(
                        camera_path, headers=credentials("back_viewer")
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.get(
                        camera_path, headers=credentials("front_viewer")
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.post(
                        command_path,
                        headers=credentials("front_viewer"),
                        json={"value": None},
                    ).status_code,
                    403,
                )
                self.assertEqual(MockPatch.actions, [])
                self.assertEqual(
                    client.post(
                        command_path,
                        headers=credentials("admin"),
                        json={"value": None},
                    ).json(),
                    {"success": True},
                )
                self.assertEqual(MockPatch.actions, [("stop", None, None)])
