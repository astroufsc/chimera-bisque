# SPDX-FileCopyrightText: 2026-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Driver for the TheSkyX autoguider (ccdsoftAutoguider) scripting object.

Mirrors the Autoguide tab of the TheSkyX Camera window: take photo,
auto-select a guide star from the image inventory, autoguide.

Reference: https://www.bisque.com/wp-content/scriptthesky/classccdsoft_camera.html
"""

import time
from enum import IntEnum

from chimera_bisque.instruments.theskyxdriver import (
    TheSkyXCommandError,
    TheSkyXConnectionError,
    TheSkyXScriptingClient,
)


class CameraState(IntEnum):
    """ccdsoftCameraState enumeration (documentation order)."""

    IDLE = 0  # cdStateNone
    TAKE_PICTURE = 1  # cdStateTakePicture
    TAKE_PICTURE_SERIES = 2  # cdStateTakePictureSeries
    FOCUS = 3  # cdStateFocus
    MOVE_GUIDE_STAR = 4  # cdStateMoveGuideStar
    AUTOGUIDE = 5  # cdStateAutoGuide
    CALIBRATE = 6  # cdStateCalibrate
    TAKE_COLOR = 7  # cdStateTakeColor
    AUTOFOCUS = 8  # cdStateAutoFocus
    AUTOFOCUS2 = 9  # cdStateAutoFocus2


class TheSkyXNoStarError(TheSkyXCommandError):
    """No suitable guide star found in the autoguider image."""


class TheSkyXAutoguiderDriver(TheSkyXScriptingClient):
    """Controls the TheSkyX autoguider camera over the scripting socket.

    All operations run asynchronously on the TheSkyX side (Asynchronous=1)
    so a long exposure or calibration never blocks the scripting engine;
    completion is polled via IsExposureComplete/State.
    """

    def connect(self) -> None:
        """Connect TheSkyX to the autoguider camera."""
        self.log.info(f"Connecting to TheSkyX autoguider at {self.host}:{self.port}")
        command = """
            ccdsoftAutoguider.Connect();
            ccdsoftAutoguider.Asynchronous = 1;
            Out = "OK";
        """
        self._send_command(command)
        self._is_connected = True
        self.log.info("Connected to TheSkyX autoguider")

    def disconnect(self) -> None:
        """Disconnect TheSkyX from the autoguider camera."""
        if not self._is_connected:
            return
        try:
            command = """
                ccdsoftAutoguider.Disconnect();
                Out = "OK";
            """
            self._send_command(command)
        except (TheSkyXConnectionError, TheSkyXCommandError) as e:
            self.log.warning(f"Error disconnecting TheSkyX autoguider: {e}")
        finally:
            self._is_connected = False

    def get_state(self) -> CameraState:
        """Current autoguider camera state."""
        command = """
            Out = ccdsoftAutoguider.State;
        """
        result = self._send_command(command)
        try:
            return CameraState(int(result))
        except ValueError:
            raise TheSkyXCommandError(f"Unexpected autoguider state: {result!r}")

    def take_image(self, exptime: float, timeout_margin: float = 30.0) -> None:
        """Take a photo with the autoguider camera and wait for it.

        Equivalent to the "Take Photo" button on the Autoguide tab.
        """
        self._ensure_connected()
        # AutoSaveOn is required: AttachToActiveAutoguider() opens the image
        # from disk and fails with error 709 when the exposure was not saved.
        command = f"""
            ccdsoftAutoguider.Asynchronous = 1;
            ccdsoftAutoguider.AutoSaveOn = 1;
            ccdsoftAutoguider.Frame = 1;
            ccdsoftAutoguider.Delay = 0;
            ccdsoftAutoguider.Subframe = 0;
            ccdsoftAutoguider.ExposureTime = {float(exptime)};
            ccdsoftAutoguider.TakeImage();
            Out = "OK";
        """
        self._send_command(command)

        deadline = time.monotonic() + float(exptime) + timeout_margin
        while time.monotonic() < deadline:
            if self.is_exposure_complete():
                return
            time.sleep(0.5)
        raise TheSkyXCommandError(
            f"Autoguider exposure did not complete after {exptime + timeout_margin}s."
        )

    def is_exposure_complete(self) -> bool:
        command = """
            Out = ccdsoftAutoguider.IsExposureComplete;
        """
        return int(self._send_command(command)) == 1

    def select_guide_star(self, edge_margin: float = 0.05) -> tuple[float, float]:
        """Auto-select a guide star on the last autoguider image.

        Equivalent to the "auto-select star" UI action: runs the source
        inventory on the last image, picks the brightest star away from the
        edges and sets it as the autoguider guide star.

        Returns the (x, y) guide star pixel position.

        Raises:
            TheSkyXNoStarError: If no suitable star is found.
        """
        self._ensure_connected()
        command = f"""
            ccdsoftAutoguiderImage.AttachToActiveAutoguider();
            ccdsoftAutoguiderImage.ShowInventory();
            var X = ccdsoftAutoguiderImage.InventoryArray(0);
            var Y = ccdsoftAutoguiderImage.InventoryArray(1);
            var mag = ccdsoftAutoguiderImage.InventoryArray(2);
            var width = ccdsoftAutoguiderImage.WidthInPixels;
            var height = ccdsoftAutoguiderImage.HeightInPixels;
            var marginX = width * {float(edge_margin)};
            var marginY = height * {float(edge_margin)};
            var best = -1;
            for (var i = 0; i < X.length; i++) {{
                if (X[i] < marginX || X[i] > width - marginX) continue;
                if (Y[i] < marginY || Y[i] > height - marginY) continue;
                if (best < 0 || mag[i] < mag[best]) best = i;
            }}
            if (best < 0) {{
                Out = "NOSTAR";
            }} else {{
                ccdsoftAutoguider.GuideStarX = X[best];
                ccdsoftAutoguider.GuideStarY = Y[best];
                Out = String(X[best]) + " " + String(Y[best]);
            }}
        """
        result = self._send_command(command)
        if result == "NOSTAR":
            raise TheSkyXNoStarError("No suitable guide star found in image.")

        try:
            x, y = (float(v) for v in result.split())
        except ValueError:
            raise TheSkyXCommandError(f"Invalid guide star response: {result!r}")

        self.log.info(f"Guide star selected at ({x:.2f}, {y:.2f})")
        return x, y

    def get_guide_star(self) -> tuple[float, float]:
        """Current guide star pixel position."""
        command = """
            Out = String(ccdsoftAutoguider.GuideStarX) + " " +
                  String(ccdsoftAutoguider.GuideStarY);
        """
        result = self._send_command(command)
        x, y = (float(v) for v in result.split())
        return x, y

    def set_guide_star(self, x: float, y: float) -> None:
        """Set the guide star pixel position for the next Autoguide()."""
        command = f"""
            ccdsoftAutoguider.GuideStarX = {float(x)};
            ccdsoftAutoguider.GuideStarY = {float(y)};
            Out = "OK";
        """
        self._send_command(command)

    def calibrate(self, exptime: float, timeout: float = 300.0) -> None:
        """Calibrate the autoguider and wait for completion."""
        self._ensure_connected()
        command = f"""
            ccdsoftAutoguider.Asynchronous = 1;
            ccdsoftAutoguider.AutoguiderExposureTime = {float(exptime)};
            ccdsoftAutoguider.Calibrate(0);
            Out = "OK";
        """
        self._send_command(command)

        # wait for the calibration run to start and finish
        deadline = time.monotonic() + timeout
        time.sleep(1.0)
        while time.monotonic() < deadline:
            if self.get_state() != CameraState.CALIBRATE:
                self.log.info("Autoguider calibration finished")
                return
            time.sleep(1.0)
        raise TheSkyXCommandError(f"Calibration did not finish after {timeout}s.")

    def start_autoguide(self, exptime: float) -> None:
        """Start autoguiding on the selected guide star (asynchronous).

        Equivalent to the "Autoguide" button. Uses the stored calibration.
        """
        self._ensure_connected()
        command = f"""
            ccdsoftAutoguider.Asynchronous = 1;
            ccdsoftAutoguider.AutoguiderExposureTime = {float(exptime)};
            ccdsoftAutoguider.ExposureTime = {float(exptime)};
            ccdsoftAutoguider.Autoguide();
            Out = "OK";
        """
        self._send_command(command)
        self.log.info("Autoguiding started")

    def abort(self) -> None:
        """Abort any running autoguider operation (exposure/calibration/guiding)."""
        self._ensure_connected()
        command = """
            ccdsoftAutoguider.Abort();
            Out = "OK";
        """
        self._send_command(command)
        self.log.info("Autoguider operation aborted")

    def _ensure_connected(self) -> None:
        if not self._is_connected:
            raise TheSkyXConnectionError("Not connected to TheSkyX autoguider")
