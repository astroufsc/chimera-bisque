# Based on http://www.ascom-standards.org/Help/Developer/html/AllMembers_T_ASCOM_DriverAccess_Camera.htm
from chimera.core.site import datetimeFromJD

__author__ = 'william'

import sys
import logging
import numpy as np
from chimera.core.lock import lock
from chimera.instruments.camera import CameraBase
from chimera.core.exceptions import ChimeraException
from chimera.interfaces.camera import CameraFeature, CCD, ReadoutMode, CameraStatus, Shutter

log = logging.getLogger(__name__)

if sys.platform == "win32":
    sys.coinit_flags = 0
    from win32com.client import Dispatch
    from pywintypes import com_error
else:
    log.warning("Not on Windows. ASCOM CAMERA will not work.")


class CCDSoftCamera(CameraBase):
    __config__ = {'model': 'CCDSoft camera',
                  'ccd_width': 4096,
                  'ccd_height': 4096,
                  'ccd_pixsize_x': 9,  # microns
                  'ccd_pixsize_y': 9,  # microns
                  'min_exptime': 0.00001  # minimum exptime in seconds
                  }

    def __init__(self):
        CameraBase.__init__(self)

        self._n_attempts = 0

    def __start__(self):

        self.open()

        # my internal CCD code
        self._MY_CCD = 1 << 1
        self._MY_ADC = 1 << 2
        self._MY_READOUT_MODE = 1 << 3

        self._ccds = {self._MY_CCD: CCD.IMAGING}

        self._adcs = {"12 bits": self._MY_ADC}

        self._binnings = {"1x1": 0,
                          "2x2": 1,
                          "3x3": 2,
                          "9x9": 3,
                          "10x10": 4}

        self._binning_factors = {"1x1": 1,
                                 "2x2": 2,
                                 "3x3": 3,
                                 "9x9": 9,
                                 "10x10": 10}

        self._supports = {CameraFeature.TEMPERATURE_CONTROL: True,
                          CameraFeature.PROGRAMMABLE_GAIN: False,
                          CameraFeature.PROGRAMMABLE_OVERSCAN: False,
                          CameraFeature.PROGRAMMABLE_FAN: False,
                          CameraFeature.PROGRAMMABLE_LEDS: False,
                          CameraFeature.PROGRAMMABLE_BIAS_LEVEL: False}

        self._readout_modes = [0]

        self._readoutModes = {self._MY_CCD: {}}
        i_mode_tot = 0
        for i_mode in range(len(self._readout_modes)):
            for binning, i_mode in self._binnings.iteritems():
                readoutMode = ReadoutMode()
                vbin, hbin = [int(v) for v in binning.split('x')]
                readoutMode.mode = i_mode
                # TODO:  readoutMode.gain = self._ccdsoft.ElectronsPerADU
                readoutMode.width = self["ccd_width"] / hbin
                readoutMode.height = self["ccd_height"] / vbin
                readoutMode.pixelWidth = self['ccd_pixsize_x'] * hbin
                readoutMode.pixelHeight = self['ccd_pixsize_y'] * vbin
                self._readoutModes[self._MY_CCD].update({i_mode: readoutMode})
                i_mode_tot += 1

        self.setHz(2)

    def __stop__(self):
        self.close()

    def close(self):
        self._ccdsoft.Disconnect()

    def open(self):
        '''
        Connects to ASCOM server
        :return:
        '''
        self.log.debug('Starting CCDSoft camera')
        self._ccdsoft = Dispatch("CCDSoft.Camera")
        try:
            self._ccdsoft.Connect()
            self._ccdsoft.Asynchronous = 1
        except com_error:
            raise ChimeraException("Could not configure camera.")

    def _expose(self, request):
        """
        .. method:: expose(request=None, **kwargs)

            Start an exposure based upon the specified image request or
            create a new image request from kwargs

            :keyword request: ImageRequest object
            :type request: ImageRequest
        """
        self.exposeBegin(request)

        if request['shutter'] == Shutter.OPEN:
            img_type = 1  # light
        elif request['shutter'] == Shutter.CLOSE:
            img_type = 3  # dark
        elif request['shutter'] == Shutter.LEAVE_AS_IS:
            raise ChimeraException('Not supported to leave as is shutter.')

        # Can only take images of exptime > minexptime.
        if request["exptime"] < self['min_exptime']:
            request["exptime"] = self['min_exptime']

        mode, binning, top, left, width, height = self._getReadoutModeInfo(request["binning"], request["window"])
        # Binning
        vbin, hbin = [int(v) for v in binning.split('x')]
        self._ccdsoft.BinX = vbin
        self._ccdsoft.BinY = hbin

        # TODO: Subframing
        self._ccdsoft.Subframe = False
        # self._ccdsoft.SubframeBottom = top
        # self._ccdsoft.SubframeLeft = left
        # self._ccdsoft.SubframeRight = right
        # self._ccdsoft.SubframeTop = top

        # Start Exposure...
        self._ccdsoft.ImageReduction = 0  # Disable any possible data reduction
        self._ccdsoft.ExposureTime = request["exptime"]
        self._ccdsoft.Frame = img_type
        self._ccdsoft.TakeImage()

        status = CameraStatus.OK

        while not bool(self._ccdsoft.IsExposureComplete):
            # [ABORT POINT]
            if self.abort.isSet():
                self._ccdsoft.Abort()
                status = CameraStatus.ABORTED
                break

        self.exposeComplete(request, status)

    def _readout(self, request):
        self.readoutBegin(request)

        img = Dispatch("CCDSoft.Image")
        img.AttachToActiveImager()
        pix = np.transpose(np.array(img.DataArray))

        (mode, binning, top, left, width, height) = self._getReadoutModeInfo(request["binning"], request["window"])

        request.headers.append(('GAIN', str(mode.gain), 'Electronic gain in photoelectrons per ADU'))

        proxy = self._saveImage(request, pix, {
            "frame_start_time": datetimeFromJD(img.JulianDay),
            "frame_temperature": self.getTemperature(),
            "binning_factor": self._binning_factors[binning]})

        # [ABORT POINT]
        if self.abort.isSet():
            self.readoutComplete(None, CameraStatus.ABORTED)
            return None

        self.readoutComplete(proxy, CameraStatus.OK)
        return proxy

    @lock
    def startFan(self, rate=None):
        return False

    @lock
    def stopFan(self):
        return False

    def isFanning(self):
        return False

    def getCCDs(self):
        return self._ccds

    def getCurrentCCD(self):
        return self._MY_CCD

    def getBinnings(self):
        return self._binnings

    def getADCs(self):
        return self._adcs

    def getPhysicalSize(self):
        return self["ccd_width"], self["ccd_height"]

    def getPixelSize(self):
        # TODO: return self._pixelWidth, self._pixelHeight
        return 9, 9

    def getOverscanSize(self, ccd=None):
        return 0, 0  # FIXME

    def getReadoutModes(self):
        return self._readoutModes

    def supports(self, feature=None):
        return self._supports[feature]

    @lock
    def startCooling(self, setpoint):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        self._ccdsoft.ShutDownTemperatureRegulationOnDisconnect = 0
        self._ccdsoft.TemperatureSetPoint = setpoint
        self._ccdsoft.RegulateTemperature = 1
        return True

    @lock
    def stopCooling(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        self._ccdsoft.RegulateTemperature = 0

    def isCooling(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        return bool(self._ccdsoft.RegulateTemperature)

    @lock
    def getTemperature(self):
        if not self.supports(CameraFeature.TEMPERATURE_CONTROL):
            return False
        return self._ccdsoft.Temperature

    def getSetPoint(self):
        return self._ccdsoft.TemperatureSetPoint


class InvalidExposureTime(ChimeraException):
    pass
