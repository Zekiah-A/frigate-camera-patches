"""Owner-scoped, short-lived ONVIF joystick movement."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)
CAMERA_TIMEOUT = timedelta(seconds=1)
LEASE_SECONDS = 0.4
VELOCITY_LIMIT = 0.2
VELOCITY_SPACE = "http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace"


def supports_joystick(options: Any) -> bool:
    """Require signed generic velocity and honor advertised timeout limits."""
    timeouts = getattr(options, "PTZTimeout", None)
    if timeouts is not None:
        minimum = getattr(timeouts, "Min", None)
        maximum = getattr(timeouts, "Max", None)
        if not (
            isinstance(minimum, timedelta)
            and isinstance(maximum, timedelta)
            and minimum <= CAMERA_TIMEOUT <= maximum
        ):
            return False

    spaces = getattr(options, "Spaces", None)
    return any(
        space.URI == VELOCITY_SPACE
        and all(
            bounds is not None
            and bounds.Min is not None
            and bounds.Max is not None
            and bounds.Min <= -VELOCITY_LIMIT <= VELOCITY_LIMIT <= bounds.Max
            for bounds in (space.XRange, space.YRange)
        )
        for space in getattr(spaces, "ContinuousPanTiltVelocitySpace", None) or []
    )


def parse_joystick(payload: object) -> tuple[str, int, float, float] | None:
    """Validate a gesture command and cap the magnitude of its velocity."""
    if not isinstance(payload, dict):
        return None
    phase, gesture = payload.get("phase"), payload.get("gesture")
    if phase not in ("start", "move", "stop") or type(gesture) is not int:
        return None
    if not 0 <= gesture <= 2**53 - 1:
        return None
    if phase == "stop":
        return phase, gesture, 0.0, 0.0
    x, y = payload.get("x"), payload.get("y")
    # Range checks also reject non-finite values and arbitrarily large integers.
    if not all(type(axis) in (int, float) and -1 <= axis <= 1 for axis in (x, y)):
        return None
    limit = VELOCITY_LIMIT / max(1, math.hypot(x, y))
    return phase, gesture, x * limit, y * limit


class JoystickLease:
    """Serialize the latest sample and its final stop on the ONVIF event loop."""

    def __init__(
        self,
        owner: object,
        gesture: int,
        move: Callable[[float, float], Awaitable[None]],
        stop: Callable[[], Awaitable[None]],
    ) -> None:
        self.owner = owner
        self.gesture = gesture
        self._move = move
        self._stop = stop
        self._deadline = 0.0
        self._latest = (0.0, 0.0)
        self._wakeup = asyncio.Event()
        self._released = False

    def update(self, x: float, y: float, deadline: float) -> None:
        """Retain only the newest vector and extend the current lease."""
        if self._deadline and self._deadline <= asyncio.get_running_loop().time():
            self.release()
        if not self._released:
            self._deadline = deadline
            self._latest = (x, y)
            self._wakeup.set()

    def release(self) -> None:
        """Invalidate queued movement and wake the worker to stop the camera."""
        self._released = True
        self._wakeup.set()

    async def run(self) -> None:
        """Send live velocity samples and stop after release, timeout or failure."""
        loop = asyncio.get_running_loop()
        moving = False
        try:
            while (
                not self._released and (remaining := self._deadline - loop.time()) > 0
            ):
                vector = self._latest
                self._wakeup.clear()
                if vector != (0.0, 0.0):
                    # A lost response could still mean that the camera moved.
                    moving = True
                    await asyncio.wait_for(self._move(*vector), remaining)
                elif moving:
                    await asyncio.wait_for(self._stop(), CAMERA_TIMEOUT.total_seconds())
                    moving = False

                if self._wakeup.is_set():
                    continue
                remaining = self._deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._wakeup.wait(), remaining)
                except TimeoutError:
                    # Recheck the deadline in case a sample renewed it concurrently.
                    continue
        except Exception:
            logger.warning("ONVIF joystick request failed")
        finally:
            self.release()
            if moving:
                try:
                    await asyncio.wait_for(self._stop(), CAMERA_TIMEOUT.total_seconds())
                except Exception:
                    logger.warning("ONVIF joystick stop failed")
