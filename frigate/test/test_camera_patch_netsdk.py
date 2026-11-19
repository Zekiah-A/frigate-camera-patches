"""Offline coverage of the ANNKE I51CP NetSDK command and readback shapes."""

import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from frigate.camera_patches.provider import PatchError
from frigate.camera_patches.providers.netsdk import NetSdkPatch

STATUS = "/netsdk/v2/Stat"
AUDIO = "/netsdk/audio/encode/channel/101"
ORIENTATION = "/netsdk/video/input/channel/1"
IMAGE = "/netsdk/image"
LIGHT = "/netsdk/v2/R/LightManCtrl"
SIREN = "/netsdk/v2/R/SoundManCtrl"


class TestNetSdkPatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Minimal sanitized shapes from the I51CP companion implementation.
        self.camera_data = {
            STATUS: {"Light": "OFF", "Sound": "OFF"},
            AUDIO: {"enabled": True},
            ORIENTATION: {"flipEnabled": True, "mirrorEnabled": False},
            IMAGE: {"irCutFilter": {"irCutMode": "night", "irCutControlMode": "auto"}},
        }
        self.requests = []
        self.special_response = {}
        self.ignore_light_command = False
        self.ignore_toggle_command = False
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.camera_api)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.settings = {
            "model": "annke_i51cp",
            "host": self.server.host,
            "port": self.server.port,
            "username": "example",
            "password_env": "FRIGATE_NETSDK_TEST_PASSWORD",
        }
        with patch.dict(
            "frigate.config.env.FRIGATE_ENV_VARS",
            {"FRIGATE_NETSDK_TEST_PASSWORD": ""},
            clear=True,
        ):
            self.provider = NetSdkPatch("test_camera", self.settings)
        self.addAsyncCleanup(self.provider.close)

    async def camera_api(self, request: web.Request) -> web.Response:
        self.assertEqual(request.headers.get("Authorization"), "Basic ZXhhbXBsZTo=")
        body = await request.json() if request.method == "PUT" else None
        self.requests.append((request.method, request.path, body))
        if request.path in self.special_response:
            return self.special_response[request.path]()
        if request.method == "GET":
            return web.json_response(self.camera_data[request.path])
        if request.path == LIGHT:
            if not self.ignore_light_command:
                self.camera_data[STATUS]["Light"] = body["Operate"]
        elif request.path != SIREN and not self.ignore_toggle_command:
            self.camera_data[request.path].update(body)
        return web.json_response({"statusCode": 0})

    async def test_readback_keeps_live_state_and_settings_separate(self):
        self.assertEqual(
            await self.provider.state(),
            {
                "light": "OFF",
                "siren": "OFF",
                "microphone": True,
                "vertical_flip": True,
                "horizontal_mirror": False,
            },
        )
        self.camera_data[STATUS] = {}
        self.camera_data[AUDIO] = {"enabled": "invalid"}
        state = await self.provider.state()
        self.assertEqual(state["light"], "UNKNOWN")
        self.assertIsNone(state["microphone"])

    async def test_toggles_read_back_each_value_and_only_write_the_requested_field(
        self,
    ):
        await self.provider.command("horizontal_mirror", True)
        await self.provider.command("vertical_flip", False)
        await self.provider.command("microphone", False)
        self.assertEqual(
            self.requests[::2],
            [
                ("PUT", ORIENTATION, {"mirrorEnabled": True}),
                ("PUT", ORIENTATION, {"flipEnabled": False}),
                ("PUT", AUDIO, {"enabled": False}),
            ],
        )
        self.assertEqual(
            self.camera_data[ORIENTATION],
            {"flipEnabled": False, "mirrorEnabled": True},
        )
        self.ignore_toggle_command = True
        with self.assertRaisesRegex(PatchError, "did not match readback"):
            await self.provider.command("microphone", True)

    async def test_timed_light_and_smart_restore_preserve_filter_fields(self):
        await self.provider.command("light_on", None, 45)
        self.assertEqual(self.camera_data[STATUS]["Light"], "ON")
        await self.provider.command("light_off", None)
        self.assertEqual(self.camera_data[STATUS]["Light"], "OFF")
        await self.provider.command("light_auto", None)
        self.assertEqual(
            [request for request in self.requests if request[0] == "PUT"],
            [
                ("PUT", LIGHT, {"Operate": "ON", "DurSec": 45}),
                ("PUT", LIGHT, {"Operate": "OFF", "DurSec": 300}),
                (
                    "PUT",
                    IMAGE,
                    {"irCutFilter": {"irCutMode": "smart", "irCutControlMode": "auto"}},
                ),
                ("PUT", LIGHT, {"Operate": "RECOVER", "DurSec": 5}),
            ],
        )

    async def test_light_ack_requires_actual_lamp_readback(self):
        self.ignore_light_command = True
        with self.assertRaisesRegex(PatchError, "did not match the command"):
            await self.provider.command("light_on", None)
        self.assertEqual(
            self.requests[0], ("PUT", LIGHT, {"Operate": "ON", "DurSec": 300})
        )
        self.assertEqual(self.requests[1:], [("GET", STATUS, None)] * 5)

    async def test_siren_ack_does_not_fabricate_runtime_sound_state(self):
        await self.provider.command("siren_on", None)
        self.assertEqual((await self.provider.state())["siren"], "OFF")
        self.special_response[STATUS] = lambda: web.Response(status=503)
        with self.assertRaises(PatchError):
            await self.provider.state()
        await self.provider.command("siren_off", None)
        self.assertEqual(
            [request for request in self.requests if request[0] == "PUT"],
            [
                ("PUT", SIREN, {"Operate": "ON", "DurSec": 3}),
                ("PUT", SIREN, {"Operate": "OFF", "DurSec": 1}),
            ],
        )

    async def test_invalid_values_never_reach_camera(self):
        for control, value, duration in (
            ("siren_on", None, True),
            ("siren_on", None, 11),
            ("light_on", None, 0),
            ("light_on", None, 65537),
            ("light_auto", None, 1),
            ("horizontal_mirror", 1, None),
            ("microphone", True, 3),
            ("not_a_control", None, None),
        ):
            with self.subTest(control=control, value=value, duration=duration):
                with self.assertRaises(PatchError):
                    await self.provider.command(control, value, duration)
        self.assertEqual(self.requests, [])

    async def test_rejects_redirect_without_moving_request_or_credentials(self):
        self.special_response[STATUS] = lambda: web.HTTPFound(location=AUDIO)
        with self.assertRaisesRegex(PatchError, "Camera rejected the NetSDK request"):
            await self.provider.state()
        self.assertEqual(self.requests, [("GET", STATUS, None)])

    async def test_bad_device_responses_use_safe_errors(self):
        for response, error in (
            (
                lambda: web.json_response(
                    {"statusCode": 7, "message": "raw camera value"}
                ),
                "Camera rejected the NetSDK command",
            ),
            (lambda: web.Response(text="{"), "Invalid camera NetSDK response"),
            (lambda: web.json_response([]), "Invalid camera NetSDK response"),
            (lambda: web.Response(body=b"x" * 65537), "Invalid camera NetSDK response"),
        ):
            self.special_response[STATUS] = response
            with self.assertRaisesRegex(PatchError, error):
                await self.provider.state()

    def test_explicit_model_endpoint_and_secret_reference_are_required(self):
        self.provider.validate_settings(self.settings)
        for invalid in (
            {"model": "unspecified"},
            {"host": "http://camera.test/settings"},
            {"host": "camera.test:8010"},
            {"port": 0},
            {"password_env": "inline-password"},
            {"password_env": "FRIGATE_JWT_SECRET"},
            {"username": "bad:username"},
            {"password": "inline-password"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.provider.validate_settings({**self.settings, **invalid})
        with (
            patch.dict("frigate.config.env.FRIGATE_ENV_VARS", {}, clear=True),
            self.assertRaisesRegex(PatchError, "credential is unavailable"),
        ):
            NetSdkPatch("test_camera", self.settings)
