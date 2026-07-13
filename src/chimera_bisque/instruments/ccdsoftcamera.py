# SPDX-FileCopyrightText: 2010-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Chimera camera/filter-wheel driver for CCDSoft through Windows COM.

Based on http://www.bisque.com/helpold/CCDSoft/ccdsoft.htm#afxcore/scripting.htm
"""

import logging
import sys

import numpy as np
from astropy.time import Time
from chimera.core.exceptions import ChimeraException
from chimera.core.lock import lock
from chimera.instruments.camera import CameraBase
from chimera.instruments.filterwheel import FilterWheelBase
from chimera.interfaces.camera import (
    CameraFeature,
    CameraStatus,
    ReadoutMode,
    Shutter,
)
from chimera.interfaces.filterwheel import InvalidFilterPositionException

log = logging.getLogger(__name__)

if sys.platform == "win32":
    sys.coinit_flags = 0
    from pywintypes import com_error
    from win32com.client import Dispatch
else:
    log.warning("Not on win32. CCDSoft COM camera driver will not work.")
    # Placeholders so the module imports on non-Windows; the COM drivers only
    # work when pywin32 and CCDSoft are present (see the `windows` extra).
    Dispatch = None
    com_error = Exception


class InvalidExposureTime(ChimeraException):
    pass


class CCDSoftCamera(CameraBase, FilterWheelBase):
    __config__ = {
        "model": "CCDSoft camera",
        "ccd_width": 4096,
        "ccd_height": 4096,
        "ccd_pixsize_x": 9,  # microns
        "ccd_pixsize_y": 9,  # microns
        "min_exptime": 0.00001,  # minimum exptime in seconds
        "device": "Software",
        "filter_wheel_model": "Unknown",
    }

    def __init__(self):
        CameraBase.__init__(self)

        self._ccdsoft = None
        self._n_attempts = 0

        self._binnings = {"1x1": 0, "2x2": 1, "3x3": 2, "9x9": 3, "10x10": 4}

        self._binning_factors = {"1x1": 1, "2x2": 2, "3x3": 3, "9x9": 9, "10x10": 10}

        self._supports = {
            CameraFeature.TEMPERATURE_CONTROL: True,
            CameraFeature.PROGRAMMABLE_GAIN: False,
            CameraFeature.PROGRAMMABLE_OVERSCAN: False,
            CameraFeature.PROGRAMMABLE_FAN: False,
            CameraFeature.PROGRAMMABLE_LEDS: False,
            CameraFeature.PROGRAMMABLE_BIAS_LEVEL: False,
        }

        self._readout_modes = {}

    def __start__(self):
        self.open()

        self._readout_modes = {}
        for binning, bin_id in self._binnings.items():
            vbin, hbin = [int(v) for v in binning.split("x")]
            readout_mode = ReadoutMode()
            readout_mode.mode = bin_id
            # TODO: readout_mode.gain = self._ccdsoft.ElectronsPerADU
            readout_mode.width = self["ccd_width"] // hbin
            readout_mode.height = self["ccd_height"] // vbin
            readout_mode.pixel_width = self["ccd_pixsize_x"] * hbin
            readout_mode.pixel_height = self["ccd_pixsize_y"] * vbin
            self._readout_modes[bin_id] = readout_mode

        self.set_hz(2)

    def __stop__(self):
        self.close()

    def close(self):
        self._ccdsoft.Disconnect()

    def open(self):
        """Connect to the CCDSoft server."""
        self.log.debug("Starting CCDSoft camera")
        self._ccdsoft = Dispatch("CCDSoft.Camera")
        try:
            self._ccdsoft.Connect()
            self._ccdsoft.Asynchronous = 1
        except com_error:
            raise ChimeraException("Could not configure camera.")

    def _expose(self, request):
        self.expose_begin(request)

        if request["shutter"] == Shutter.OPEN:
            img_type = 1  # light
        elif request["shutter"] == Shutter.CLOSE:
            img_type = 3  # dark
        else:
            raise ChimeraException("Not supported to leave the shutter as is.")

        # Can only take images of exptime > min_exptime.
        if request["exptime"] < self["min_exptime"]:
            request["exptime"] = self["min_exptime"]

        mode, binning, top, left, width, height = self._get_readout_mode_info(
            request["binning"], request["window"]
        )
        # Binning
        vbin, hbin = [int(v) for v in binning.split("x")]
        self._ccdsoft.BinX = vbin
        self._ccdsoft.BinY = hbin

        # TODO: Subframing
        self._ccdsoft.Subframe = False

        # Start Exposure...
        self._ccdsoft.ImageReduction = 0  # Disable any possible data reduction
        self._ccdsoft.ExposureTime = request["exptime"]
        self._ccdsoft.Frame = img_type
        self._ccdsoft.TakeImage()

        status = CameraStatus.OK

        while not bool(self._ccdsoft.IsExposureComplete):
            # [ABORT POINT]
            if self.abort.is_set():
                self._ccdsoft.Abort()
                status = CameraStatus.ABORTED
                break

        self.expose_complete(request, status)

    def _readout(self, request):
        self.readout_begin(request)

        img = Dispatch("CCDSoft.Image")
        img.AttachToActiveImager()
        pix = np.transpose(np.array(img.DataArray))

        (mode, binning, top, left, width, height) = self._get_readout_mode_info(
            request["binning"], request["window"]
        )

        request.headers.append(
            ("GAIN", str(mode.gain), "Electronic gain in photoelectrons per ADU")
        )

        image = self._save_image(
            request,
            pix,
            {
                "frame_start_time": Time(img.JulianDay, format="jd").to_datetime(),
                "frame_temperature": self.get_temperature(),
                "binning_factor": self._binning_factors[binning],
            },
        )

        # [ABORT POINT]
        if self.abort.is_set():
            self.readout_complete(None, CameraStatus.ABORTED)
            return None

        self.readout_complete(image.url(), CameraStatus.OK)
        return image

    @lock
    def start_fan(self, rate=None):
        return False

    @lock
    def stop_fan(self):
        return False

    def is_fanning(self):
        return False

    def get_binnings(self):
        return self._binnings

    def get_adcs(self):
        return {"12 bits": 0}

    def get_physical_size(self):
        return self["ccd_width"], self["ccd_height"]

    def get_pixel_size(self):
        return self["ccd_pixsize_x"], self["ccd_pixsize_y"]

    def get_overscan_size(self, ccd=None):
        return 0, 0  # FIXME

    def get_readout_modes(self):
        return self._readout_modes

    def supports(self, feature=None):
        return self._supports.get(feature, False)

    @lock
    def start_cooling(self, setpoint):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        self._ccdsoft.ShutDownTemperatureRegulationOnDisconnect = 0
        self._ccdsoft.TemperatureSetPoint = setpoint
        self._ccdsoft.RegulateTemperature = 1
        return True

    @lock
    def stop_cooling(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        self._ccdsoft.RegulateTemperature = 0

    def is_cooling(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        return bool(self._ccdsoft.RegulateTemperature)

    @lock
    def get_temperature(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        return self._ccdsoft.Temperature

    def get_set_point(self):
        return self._ccdsoft.TemperatureSetPoint

    def set_filter(self, filter):
        filter_name = str(filter).upper()

        if filter_name not in self.get_filters():
            raise InvalidFilterPositionException(f"Invalid filter {filter}.")

        self.filter_change(filter, self.get_filter())

        self._ccdsoft.FilterIndexZeroBased = self._get_filter_position(filter)

    def get_filter(self):
        return self._get_filter_name(self._ccdsoft.FilterIndexZeroBased)
