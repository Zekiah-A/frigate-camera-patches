"""Local ANNKE I51CP camera controls using fixed NetSDK endpoints."""

import asyncio
import ipaddress
import json
import re
from typing import Literal

import aiohttp
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)
from yarl import URL

from frigate.camera_patches.provider import (
    CameraPatch,
    PatchControl,
    PatchControlDuration,
    PatchError,
)

_STATUS = "/netsdk/v2/Stat"
_AUDIO = "/netsdk/audio/encode/channel/101"
_ORIENTATION = "/netsdk/video/input/channel/1"
_IMAGE = "/netsdk/image"
_LIGHT = "/netsdk/v2/R/LightManCtrl"
_SIREN = "/netsdk/v2/R/SoundManCtrl"
_RESPONSE_LIMIT = 65536

_LIGHT_DURATION = PatchControlDuration(minimum=1, maximum=65536, default=300)
_SIREN_DURATION = PatchControlDuration(minimum=1, maximum=10, default=3)
_TOGGLES = {
    "microphone": (_AUDIO, "enabled"),
    "vertical_flip": (_ORIENTATION, "flipEnabled"),
    "horizontal_mirror": (_ORIENTATION, "mirrorEnabled"),
}


class NetSdkSettings(BaseModel):
    """Settings for an explicitly configured camera model and local endpoint."""

    model_config = ConfigDict(extra="forbid", strict=True)
    model: Literal["annke_i51cp"]
    host: str
    port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(min_length=1, pattern=r"^[^:\r\n]+$")
    password_env: str = Field(pattern=r"^FRIGATE_NETSDK_[A-Z0-9_]+$")

    @field_validator("host")
    @classmethod
    def validate_host(cls, host: str) -> str:
        """Accept an IP address or hostname without URL components."""
        if "%" not in host:
            try:
                return str(ipaddress.ip_address(host))
            except ValueError:
                pass
        labels = host.removesuffix(".").split(".")
        if len(host) > 253 or any(
            not re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in labels
        ):
            raise ValueError("Expected a hostname or IP address")
        return host


class NetSdkPatch(CameraPatch):
    """NetSDK controls scoped to one configured ANNKE I51CP."""

    controls = (
        PatchControl(
            id="light_on",
            label="Light on",
            kind="button",
            duration=_LIGHT_DURATION,
            state_key="light",
            readback="device",
        ),
        PatchControl(
            id="light_off",
            label="Light off",
            kind="button",
            state_key="light",
            readback="device",
        ),
        PatchControl(
            id="light_auto",
            label="Smart light",
            kind="button",
            state_key="light",
            readback="ack",
        ),
        PatchControl(
            id="siren_on",
            label="Sound siren",
            kind="button",
            duration=_SIREN_DURATION,
            state_key="siren",
            readback="ack",
        ),
        PatchControl(
            id="siren_off",
            label="Stop siren",
            kind="button",
            state_key="siren",
            readback="ack",
        ),
        PatchControl(
            id="microphone", label="Camera microphone", kind="toggle", readback="device"
        ),
        PatchControl(
            id="vertical_flip", label="Vertical flip", kind="toggle", readback="device"
        ),
        PatchControl(
            id="horizontal_mirror",
            label="Horizontal mirror",
            kind="toggle",
            readback="device",
        ),
    )

    @classmethod
    def validate_settings(cls, settings: dict[str, JsonValue]) -> None:
        """Validate server configuration without resolving or exposing credentials."""
        try:
            NetSdkSettings.model_validate(settings)
        except ValidationError:
            raise ValueError(
                "Expected annke_i51cp model, a host, a username, a FRIGATE_NETSDK_ "
                "password environment variable name, and an optional TCP port"
            ) from None

    def __init__(self, camera_name: str, settings: dict[str, JsonValue]) -> None:
        # Import after configuration parsing because config also imports the registry.
        from frigate.config.env import FRIGATE_ENV_VARS

        super().__init__(camera_name, settings)
        try:
            config = NetSdkSettings.model_validate(settings)
        except ValidationError:
            raise PatchError(503, "Invalid ANNKE NetSDK settings") from None
        password = FRIGATE_ENV_VARS.get(config.password_env)
        if password is None:
            raise PatchError(503, "ANNKE NetSDK credential is unavailable")

        self._base_url = URL.build(scheme="http", host=config.host, port=config.port)
        self._auth = aiohttp.BasicAuth(config.username, password, encoding="utf-8")
        self._session: aiohttp.ClientSession | None = None
        self._settings_lock = asyncio.Lock()
        self._siren_lock = asyncio.Lock()

    async def _request(
        self, path: str, body: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=4),
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
            )
        method = "GET" if body is None else "PUT"
        try:
            async with self._session.request(
                method, self._base_url.with_path(path), allow_redirects=False, json=body
            ) as response:
                if not 200 <= response.status < 300:
                    raise PatchError(502, "Camera rejected the NetSDK request")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    raw.extend(chunk)
                    if len(raw) > _RESPONSE_LIMIT:
                        raise PatchError(502, "Invalid camera NetSDK response")
        except TimeoutError:
            raise PatchError(504, "Camera NetSDK request timed out") from None
        except aiohttp.ClientError:
            raise PatchError(502, "Camera NetSDK connection failed") from None
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise PatchError(502, "Invalid camera NetSDK response") from None
        if not isinstance(data, dict):
            raise PatchError(502, "Invalid camera NetSDK response")
        if data.get("statusCode", 0) != 0:
            raise PatchError(502, "Camera rejected the NetSDK command")
        return data

    async def state(self) -> dict[str, JsonValue]:
        """Read current settings and light/siren runtime state from the camera."""
        status = await self._request(_STATUS)
        audio = await self._request(_AUDIO)
        orientation = await self._request(_ORIENTATION)
        return {
            "light": self._runtime_state(status.get("Light")),
            "siren": self._runtime_state(status.get("Sound")),
            "microphone": self._boolean_setting(audio, "enabled"),
            "vertical_flip": self._boolean_setting(orientation, "flipEnabled"),
            "horizontal_mirror": self._boolean_setting(orientation, "mirrorEnabled"),
        }

    @staticmethod
    def _runtime_state(value: JsonValue) -> str:
        return str(value) if value in ("ON", "OFF") else "UNKNOWN"

    @staticmethod
    def _boolean_setting(data: dict[str, JsonValue], key: str) -> bool | None:
        value = data.get(key)
        return value if isinstance(value, bool) else None

    @staticmethod
    def _duration(seconds: int | None, limits: PatchControlDuration) -> int:
        if seconds is None:
            return limits.default
        if type(seconds) is not int or not limits.minimum <= seconds <= limits.maximum:
            raise PatchError(422, "Invalid NetSDK command duration")
        return seconds

    async def command(
        self, control: str, value: JsonValue, duration: int | None = None
    ) -> None:
        """Run an allowlisted command and verify settings when readback is available."""
        # Let siren commands proceed while other settings are being read back.
        lock = (
            self._siren_lock
            if control in ("siren_on", "siren_off")
            else self._settings_lock
        )
        async with lock:
            if control in _TOGGLES:
                if type(value) is not bool or duration is not None:
                    raise PatchError(422, "Expected a boolean NetSDK setting")
                path, key = _TOGGLES[control]
                await self._request(path, {key: value})
                if self._boolean_setting(await self._request(path), key) is not value:
                    raise PatchError(
                        502, "Camera NetSDK setting did not match readback"
                    )
                return

            if value is not None or (
                duration is not None and control not in ("light_on", "siren_on")
            ):
                raise PatchError(422, "Unexpected NetSDK button arguments")
            if control == "light_on":
                await self._set_light("ON", self._duration(duration, _LIGHT_DURATION))
            elif control == "light_off":
                await self._set_light("OFF", 300)
            elif control == "light_auto":
                await self._restore_smart_light()
            elif control == "siren_on":
                await self._request(
                    _SIREN,
                    {
                        "Operate": "ON",
                        "DurSec": self._duration(duration, _SIREN_DURATION),
                    },
                )
            elif control == "siren_off":
                await self._request(_SIREN, {"Operate": "OFF", "DurSec": 1})
            else:
                raise PatchError(422, "Unknown NetSDK control")

    async def _set_light(self, operation: str, seconds: int) -> None:
        await self._request(_LIGHT, {"Operate": operation, "DurSec": seconds})
        # The firmware can acknowledge before the lamp state changes.
        for attempt in range(5):
            if (await self._request(_STATUS)).get("Light") == operation:
                return
            if attempt < 4:
                await asyncio.sleep(0.15)
        raise PatchError(502, "Camera light status did not match the command")

    async def _restore_smart_light(self) -> None:
        filter_settings = (await self._request(_IMAGE)).get("irCutFilter")
        if not isinstance(filter_settings, dict):
            raise PatchError(502, "Invalid camera image settings")
        await self._request(
            _IMAGE, {"irCutFilter": {**filter_settings, "irCutMode": "smart"}}
        )
        await self._request(_LIGHT, {"Operate": "RECOVER", "DurSec": 5})

    async def close(self) -> None:
        """Close the local HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None
