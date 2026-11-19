"""Offline regression tests for bounded analog ONVIF movement."""

import asyncio
import math
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from frigate.config.camera.onvif import OnvifConfig
from frigate.ptz.joystick import (
    CAMERA_TIMEOUT,
    VELOCITY_LIMIT,
    VELOCITY_SPACE,
    JoystickLease,
    parse_joystick,
    supports_joystick,
)
from frigate.ptz.onvif import OnvifCommandEnum, OnvifController

CAMERA = "ptz_cam"


class TestJoystickCommands(unittest.TestCase):
    def test_diagonal_respects_velocity_cap(self):
        phase, gesture, x, y = parse_joystick(
            {"phase": "start", "gesture": 12, "x": 1, "y": -1}
        )
        self.assertEqual((phase, gesture), ("start", 12))
        self.assertAlmostEqual(math.hypot(x, y), VELOCITY_LIMIT)
        self.assertGreater(x, 0)
        self.assertLess(y, 0)

    def test_rejects_unbounded_or_non_numeric_input(self):
        for x in (True, "0.2", None, 1.1, 10**100, math.inf, -math.inf, math.nan):
            with self.subTest(x=x):
                self.assertIsNone(
                    parse_joystick({"phase": "move", "gesture": 1, "x": x, "y": 0})
                )

    def test_requires_signed_velocity_and_respects_advertised_timeout(self):
        limits = SimpleNamespace(Min=-1, Max=1)
        options = SimpleNamespace(
            PTZTimeout=SimpleNamespace(Min=CAMERA_TIMEOUT, Max=timedelta(seconds=5)),
            Spaces=SimpleNamespace(
                ContinuousPanTiltVelocitySpace=[
                    SimpleNamespace(URI=VELOCITY_SPACE, XRange=limits, YRange=limits)
                ]
            ),
        )
        self.assertTrue(supports_joystick(options))
        options.PTZTimeout.Min = timedelta(seconds=2)
        self.assertFalse(supports_joystick(options))
        options.PTZTimeout.Min = CAMERA_TIMEOUT
        limits.Min = 0
        self.assertFalse(supports_joystick(options))
        limits.Min = -1
        options.PTZTimeout = None
        self.assertTrue(supports_joystick(options))


class TestJoystickLease(unittest.IsolatedAsyncioTestCase):
    async def test_watchdog_stops_after_missing_refresh(self):
        move, stop = AsyncMock(), AsyncMock()
        lease = JoystickLease(object(), 1, move, stop)
        lease.update(0.1, 0.2, asyncio.get_running_loop().time() + 0.05)
        await asyncio.wait_for(lease.run(), timeout=1)
        move.assert_awaited_once_with(0.1, 0.2)
        stop.assert_awaited_once()

    async def test_release_discards_sample_queued_during_move(self):
        entered = asyncio.Event()
        completed = asyncio.Event()
        commands = []

        async def move(x, y):
            commands.append((x, y))
            entered.set()
            await completed.wait()

        async def stop():
            commands.append("stop")

        deadline = asyncio.get_running_loop().time() + 1
        lease = JoystickLease(object(), 1, move, stop)
        lease.update(0.1, 0.2, deadline)
        task = asyncio.create_task(lease.run())
        await entered.wait()
        lease.update(0.2, 0.0, deadline)
        lease.release()
        lease.update(0.0, 0.2, deadline)
        completed.set()
        await task
        self.assertEqual(commands, [(0.1, 0.2), "stop"])

    async def test_late_refresh_cannot_restart_expired_gesture(self):
        move, stop = AsyncMock(), AsyncMock()
        lease = JoystickLease(object(), 1, move, stop)
        now = asyncio.get_running_loop().time()
        lease.update(0.1, 0.2, now - 0.01)
        lease.update(0.1, 0.2, now + 1)
        await lease.run()
        move.assert_not_awaited()
        stop.assert_not_awaited()

    async def test_centred_stick_stops_without_releasing_gesture(self):
        first_move = asyncio.Event()
        first_stop = asyncio.Event()
        second_move = asyncio.Event()

        async def move(x, y):
            (first_move if x else second_move).set()

        async def stop():
            first_stop.set()

        deadline = asyncio.get_running_loop().time() + 1
        lease = JoystickLease(object(), 1, move, stop)
        lease.update(0.1, 0.0, deadline)
        task = asyncio.create_task(lease.run())
        try:
            await asyncio.wait_for(first_move.wait(), timeout=1)
            lease.update(0.0, 0.0, deadline)
            await asyncio.wait_for(first_stop.wait(), timeout=1)
            lease.update(0.0, 0.1, deadline)
            await asyncio.wait_for(second_move.wait(), timeout=1)
        finally:
            lease.release()
            await task


class TestOnvifJoystick(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = OnvifController.__new__(OnvifController)
        self.controller.config = SimpleNamespace(
            cameras={
                CAMERA: SimpleNamespace(enabled=True, onvif=OnvifConfig(joystick=True))
            }
        )
        self.controller.loop = asyncio.get_running_loop()
        self.controller._joysticks = {}
        self.controller._legacy_commands = {}
        self.controller._joystick_closing = False
        self.moved = asyncio.Event()
        self.ptz = SimpleNamespace(
            ContinuousMove=AsyncMock(side_effect=lambda _: self.moved.set()),
            Stop=AsyncMock(),
        )
        self.controller.cams = {
            CAMERA: {
                "features": ["pt", "joystick"],
                "init": True,
                "active": False,
                "ptz": self.ptz,
                "move_request": SimpleNamespace(ProfileToken="test_profile"),
            }
        }

    async def test_lease_owner_controls_pan_tilt_and_disconnect_stops(self):
        owner = object()
        other = object()
        receive = self.controller._receive_joystick
        deadline = self.controller.loop.time() + 1
        receive(CAMERA, owner, ("start", 5, 0.2, 0.0), deadline)
        lease, task = self.controller._joysticks[CAMERA]
        await asyncio.wait_for(self.moved.wait(), timeout=1)
        self.ptz.ContinuousMove.assert_awaited_once_with(
            {
                "ProfileToken": "test_profile",
                "Velocity": {"PanTilt": {"x": 0.2, "y": 0.0, "space": VELOCITY_SPACE}},
                "Timeout": CAMERA_TIMEOUT,
            }
        )

        receive(CAMERA, other, ("stop", 5, 0.0, 0.0), deadline)
        receive(CAMERA, owner, ("stop", 6, 0.0, 0.0), deadline)
        receive(CAMERA, other, ("start", 5, 0.0, 0.2), deadline)
        self.controller.release_joysticks(other)
        await asyncio.sleep(0)
        self.assertIs(self.controller._joysticks[CAMERA][0], lease)
        self.ptz.Stop.assert_not_awaited()

        await self.controller.handle_command_async(CAMERA, OnvifCommandEnum.zoom_in)
        self.controller.release_joysticks(owner)
        await asyncio.wait_for(task, timeout=1)
        self.ptz.ContinuousMove.assert_awaited_once()
        self.ptz.Stop.assert_awaited_once_with(
            {"ProfileToken": "test_profile", "PanTilt": True, "Zoom": False}
        )
        receive(CAMERA, owner, ("move", 5, 0.2, 0.0), deadline)
        self.assertNotIn(CAMERA, self.controller._joysticks)

    async def test_does_not_start_when_autotracking_or_legacy_movement_is_active(self):
        receive = self.controller._receive_joystick
        config = self.controller.config.cameras[CAMERA].onvif
        owner = object()
        deadline = self.controller.loop.time() + 1
        config.autotracking.enabled_in_config = True
        receive(CAMERA, owner, ("start", 5, 0.2, 0.0), deadline)
        config.autotracking.enabled_in_config = False
        self.controller.cams[CAMERA]["active"] = True
        receive(CAMERA, owner, ("start", 5, 0.2, 0.0), deadline)
        self.assertNotIn(CAMERA, self.controller._joysticks)
        self.ptz.ContinuousMove.assert_not_awaited()
