# SPDX-FileCopyrightText: 2026-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chimera autoguider instrument backed by the TheSkyX autoguider.

Drives the Autoguide tab of the TheSkyX Camera window through the
scripting socket, mirroring the usual UI sequence: take photo,
auto-select star, autoguide.

Unlike PHD2, TheSkyX has no event stream or settling notion: guiding
state is polled, and per-correction offsets are not published
(offset_complete is never raised).
"""

import random
import time

from chimera.core.chimeraobject import ChimeraObject
from chimera.core.lock import lock
from chimera.interfaces.autoguider import (
    Autoguider,
    GuiderException,
    GuiderStatus,
    StarNotFoundException,
)

from chimera_bisque.instruments.theskyxautoguiderdriver import (
    CameraState,
    TheSkyXAutoguiderDriver,
    TheSkyXNoStarError,
)
from chimera_bisque.instruments.theskyxdriver import (
    TheSkyXCommandError,
    TheSkyXConnectionError,
)

STATE_MAP = {
    CameraState.IDLE: GuiderStatus.OFF,
    CameraState.TAKE_PICTURE: GuiderStatus.OK,
    CameraState.TAKE_PICTURE_SERIES: GuiderStatus.OK,
    CameraState.FOCUS: GuiderStatus.OK,
    CameraState.MOVE_GUIDE_STAR: GuiderStatus.GUIDING,
    CameraState.AUTOGUIDE: GuiderStatus.GUIDING,
    CameraState.CALIBRATE: GuiderStatus.CALIBRATING,
    CameraState.TAKE_COLOR: GuiderStatus.OK,
    CameraState.AUTOFOCUS: GuiderStatus.OK,
    CameraState.AUTOFOCUS2: GuiderStatus.OK,
}

GUIDING_STATES = (CameraState.MOVE_GUIDE_STAR, CameraState.AUTOGUIDE)


class TheSkyXAutoguider(ChimeraObject, Autoguider):
    """Autoguider that delegates star acquisition and guide corrections
    to the autoguider camera configured in TheSkyX."""

    __config__ = {
        "skyx_host": "localhost",
        "skyx_port": 3040,
        "exptime": 2.0,  # Guide camera exposure time (s).
        "edge_margin": 0.05,  # Fraction of the frame to avoid near the edges when picking a star.
        "guide_start_timeout": 60.0,  # Max time (s) to wait for guiding to start when wait=True.
        "calibration_timeout": 300.0,  # Max time (s) to wait for a calibration run.
        # Trace every scripting round-trip at DEBUG. Off by default: the
        # pollers run several times a second and would swamp the log.
        "log_protocol": False,
    }

    def __init__(self) -> None:
        ChimeraObject.__init__(self)
        self._driver: TheSkyXAutoguiderDriver | None = None
        self._guide_star: list[float] | None = None
        self._was_guiding = False

    def __start__(self) -> None:
        self._driver = TheSkyXAutoguiderDriver(
            self.log,
            host=self["skyx_host"],
            port=int(self["skyx_port"]),
            log_protocol=bool(self["log_protocol"]),
        )
        try:
            self._driver.connect()
        except (TheSkyXConnectionError, TheSkyXCommandError) as e:
            self.log.warning(
                f"Could not connect to the TheSkyX autoguider ({e}). "
                "Will connect on first use."
            )

        # poll guiding state to detect stops from the TheSkyX UI side
        self.set_hz(1 / 5.0)

    def __stop__(self) -> None:
        if self._driver is not None:
            self._driver.disconnect()

    def control(self) -> bool:
        try:
            guiding = self.is_guiding()
        except (TheSkyXConnectionError, TheSkyXCommandError, GuiderException):
            return True

        # guiding stopped outside chimera (e.g. from the TheSkyX UI)
        if self._was_guiding and not guiding:
            self.log.warning("Guiding stopped outside chimera.")
            self.guide_stop(GuiderStatus.OFF, "stopped outside chimera")
        self._was_guiding = guiding
        return True

    # Autoguider interface

    @lock
    def start_guiding(self, recalibrate=False, wait=False):
        if self.is_guiding():
            raise GuiderException("Already guiding.")

        # same sequence as the TheSkyX UI: take photo, auto-select star,
        # (calibrate,) autoguide
        position = self.find_star()

        if recalibrate:
            self.log.info("Calibrating autoguider.")
            self._guarded(
                lambda: self._driver.calibrate(
                    float(self["exptime"]), float(self["calibration_timeout"])
                )
            )

        self.log.info("Starting TheSkyX autoguiding.")
        self._guarded(lambda: self._driver.start_autoguide(float(self["exptime"])))

        if wait:
            self._wait_guiding()

        self._was_guiding = True
        self.guide_start(position)

    def stop_guiding(self):
        self._stop(GuiderStatus.OFF)

    def abort(self):
        self._stop(GuiderStatus.ABORTED)

    def is_guiding(self):
        return self._state() in GUIDING_STATES

    def get_status(self):
        try:
            return STATE_MAP.get(self._state(), GuiderStatus.ERROR)
        except GuiderException:
            return GuiderStatus.ERROR

    @lock
    def dither(self, amount=None, ra_only=None, wait=False):
        """Dither by shifting the guide star lock position by a random
        offset and restarting the guide loop.

        Note: offsets are applied in detector pixels; ra_only shifts only
        the detector x axis, which matches right ascension only if the
        guider is aligned with the sky axes.
        """
        if not self.is_guiding():
            raise GuiderException("Cannot dither: not guiding.")

        amount = float(self["dither_amount"] if amount is None else amount)
        ra_only = bool(self["dither_ra_only"] if ra_only is None else ra_only)

        x, y = self._guarded(lambda: self._driver.get_guide_star())
        dx = random.uniform(-amount, amount)
        dy = 0.0 if ra_only else random.uniform(-amount, amount)

        self.log.info(f"Dithering guide star by ({dx:.2f}, {dy:.2f}) pixels.")

        # TheSkyX has no dither command: move the lock position and restart
        self._guarded(lambda: self._driver.abort())
        self._guarded(lambda: self._driver.set_guide_star(x + dx, y + dy))
        self._guarded(lambda: self._driver.start_autoguide(float(self["exptime"])))

        if wait:
            self._wait_guiding()

        self._guide_star = [x + dx, y + dy]
        self.dither_complete([dx, dy], GuiderStatus.OK)

    @lock
    def find_star(self):
        self._connect_if_needed()

        tries = 0
        while True:
            tries += 1
            self.log.info("Taking autoguider photo.")
            self._guarded(lambda: self._driver.take_image(float(self["exptime"])))
            try:
                x, y = self._driver.select_guide_star(float(self["edge_margin"]))
                self._guide_star = [x, y]
                self.star_acquired(self._guide_star)
                return self._guide_star
            except TheSkyXNoStarError as e:
                if tries >= int(self["max_acquire_tries"]):
                    raise StarNotFoundException(str(e)) from e
            except (TheSkyXConnectionError, TheSkyXCommandError) as e:
                raise GuiderException(f"TheSkyX star selection failed: {e}") from e

    # internals

    def _stop(self, status: GuiderStatus) -> None:
        was_guiding = self.is_guiding()
        self._guarded(lambda: self._driver.abort())
        self._was_guiding = False
        if was_guiding:
            self.guide_stop(status)

    def _state(self) -> CameraState:
        self._connect_if_needed()
        return self._guarded(lambda: self._driver.get_state())

    def _wait_guiding(self) -> None:
        deadline = time.monotonic() + float(self["guide_start_timeout"])
        while time.monotonic() < deadline:
            state = self._state()
            if state in GUIDING_STATES:
                return
            if state == CameraState.IDLE:
                raise GuiderException(
                    "Autoguider went idle instead of guiding "
                    "(star faded or guiding failed to start?)."
                )
            time.sleep(1.0)
        raise GuiderException(
            f"Guiding did not start after {self['guide_start_timeout']}s."
        )

    def _connect_if_needed(self) -> None:
        assert self._driver is not None
        if not self._driver._is_connected:
            self._guarded(lambda: self._driver.connect())

    def _guarded(self, fn):
        """Run a driver call, mapping TheSkyX errors to GuiderException."""
        assert self._driver is not None
        try:
            return fn()
        except (TheSkyXConnectionError, TheSkyXCommandError) as e:
            raise GuiderException(f"TheSkyX autoguider error: {e}") from e
