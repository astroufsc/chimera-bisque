# ! /usr/bin/env python
# -*- coding: iso-8859-1 -*-

# chimera - observatory automation system
# Copyright (C) 2007  P. Henrique Silva <henrique@astro.ufsc.br>

# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.

import sys
import threading
import subprocess
import logging
import time
from chimera.core.exceptions import ChimeraException, ObjectTooLowException
from chimera.core.lock import lock
from chimera.util.coord import Coord
from chimera.util.position import Position, Epoch
from chimera.instruments.telescope import TelescopeBase
from chimera.interfaces.telescope import PositionOutsideLimitsException, TelescopeStatus

log = logging.getLogger(__name__)

if sys.platform == "win32":
    # handle COM multithread support
    # see: Python Programming On Win32, Mark Hammond and Andy Robinson, Appendix D
    #      http://support.microsoft.com/kb/q150777/
    sys.coinit_flags = 0  # pythoncom.COINIT_MULTITHREAD
    # import pythoncom

    from win32com.client import Dispatch
    from pywintypes import com_error

else:
    log.warning("Not on win32. TheSky Telescope will not work.")
    # raise ChimeraException("Not on win32. TheSky Telescope will not work.")


def com(func):
    """
    Wrapper decorator used to handle COM objects errors.
    Every method that use COM method should be decorated.
    """

    def com_wrapper(*args, **kwargs):

        try:
            return func(*args, **kwargs)
        except com_error as e:
            raise ChimeraException(str(e))

    return com_wrapper


class TheSkyTelescope(TelescopeBase):
    # The Sky 6 methods documentation:
    # https://www.bisque.com/scriptTheSkyX/classsky6_r_a_s_c_o_m_tele.html
    # https://web.archive.org/web/20170613133053/https://www.bisque.com/scriptTheSkyX/classsky6_r_a_s_c_o_m_tele.html

    __config__ = {"model": "Software Bisque The Sky telescope",
                  "thesky": [5, 6],
                  "autoclose_thesky": True,
                  "site": "/Site/0",
                  "find_home": True}

    def __init__(self):
        TelescopeBase.__init__(self)

        self._abort = threading.Event()

        self._thesky = None
        self._telescope = None
        self._idle_time = 0.2
        self._target = None

    @com
    def __start__(self):
        self.open()
        super(TheSkyTelescope, self).__start__()
        self.set_hz(1)
        return True

    @com
    def __stop__(self):
        self.close()
        super(TheSkyTelescope, self).__stop__()
        return True

    def _get_site(self):
        return self.get_manager().get_proxy(self["site"])

    @com
    def open(self):

        try:
            if self["thesky"] == 6:
                self._thesky = Dispatch("TheSky6.RASCOMTheSky")
                self._telescope = Dispatch("TheSky6.RASCOMTele")
            else:
                self._thesky = Dispatch("TheSky.RASCOMTheSky")
                self._telescope = Dispatch("TheSky.RASCOMTele")

        except com_error:
            self.log.error(
                "Couldn't instantiate TheSky %d COM objects." % self["thesky"])
            return False

        try:

            if self["thesky"] == 6:
                self._thesky.Connect()
                self._telescope.Connect()
                if self["find_home"]:
                    self._telescope.FindHome()
            else:
                self._thesky.Connect()
                self._telescope.Connect()

            return True

        except com_error as e:
            self.log.error("Couldn't connect to TheSky. (%s)" % e)
            return False

    @com
    def close(self):
        try:
            if self["autoclose_thesky"]:
                self._thesky.Disconnect()
                self._thesky.DisconnectTelescope()
                self._telescope.Disconnect()
                self._thesky.Quit()
            else:
                self.park()
        except com_error:
            self.log.error("Couldn't disconnect to TheSky.")
            return False

        if self["autoclose_thesky"]:
            if self["thesky"] == 5:
                # kill -9 on Windows
                time.sleep(2)
                subprocess.call(["TASKKILL", "/IM", "Sky.exe", "/F"])
            else:
                time.sleep(2)
                subprocess.call(["TASKKILL", "/IM", "TheSky6.exe", "/F"])

    @com
    def get_ra(self):
        self._telescope.GetRaDec()
        return self._telescope.dRa

    @com
    def get_dec(self):
        self._telescope.GetRaDec()
        return self._telescope.dDec

    @com
    def get_az(self):
        self._telescope.GetAzAlt()
        return self._telescope.dAz

    @com
    def get_alt(self):
        self._telescope.GetAzAlt()
        return self._telescope.dAlt

    @com
    def get_position_ra_dec(self):
        self._telescope.GetRaDec()
        return self._telescope.dRa, self._telescope.dDec

    @com
    def get_position_alt_az(self):
        self._telescope.GetAzAlt()
        return self._telescope.dAlt, self._telescope.dAz

    @com
    def get_target_ra_dec(self):
        if not self._target:
            return self.get_position_ra_dec()
        return self._target.ra.h, self._target.dec.d

    @com
    def slew_to_ra_dec(self, ra, dec):

        self._validate_ra_dec(ra, dec)

        if self.is_slewing():
            return False

        self._target = Position.from_ra_dec(ra, dec)
        self._abort.clear()

        try:
            self._telescope.Asynchronous = 1

            position_now = self._target.toEpoch(Epoch.NOW)
            ra_now = position_now.ra.H
            dec_now = position_now.dec.D

            self.slew_begin(ra_now, dec_now, TelescopeStatus.OK)
            self._telescope.SlewToRaDec(ra_now, dec_now, "chimera")

            status = TelescopeStatus.OK

            while not self._telescope.IsSlewComplete:

                # [ABORT POINT]
                if self._abort.is_set():
                    status = TelescopeStatus.ABORTED
                    break

                time.sleep(self._idle_time)

            self.start_tracking()

            self.slew_complete(*self.get_position_ra_dec(), status)

        except com_error:
            raise PositionOutsideLimitsException("Position outside limits.")

        return True

    @com
    def slew_to_alt_az(self, position):
        site = self._get_site()
        if self.slew_to_ra_dec(Position.alt_az_to_ra_dec(position, site['latitude'], site.lst()).to_epoch(Epoch.NOW)):
            self.stop_tracking()
            return True
        return False

    @com
    def abort_slew(self):
        if self.is_slewing():
            self._abort.set()
            time.sleep(self._idle_time)
            self._telescope.Abort()
            return True

        return False

    @com
    def is_slewing(self):
        return self._telescope.IsSlewComplete == 0

    @com
    def is_tracking(self):
        return self._telescope.IsTracking == 1

    @com
    def park(self):
        self._telescope.Park()

    @com
    def unpark(self):
        self._telescope.Connect()
        if self["find_home"]:
            self._telescope.FindHome()

    @com
    def _find_home(self):
        self._telescope.FindHome()

    @com
    def is_parked(self):
        # This information is not available on TheSky ver <= 6.
        return False

    @com
    def start_tracking(self):
        self._telescope.SetTracking(1, 1, 0, 0)

    @com
    def stop_tracking(self):
        self._telescope.SetTracking(0, 1, 0, 0)

    @com
    def move_east(self, offset, slewRate=None):
        self._telescope.Asynchronous = 0
        self._telescope.Jog(offset / 60.0, 'East')
        self._telescope.Asynchronous = 1

    @com
    def move_west(self, offset, slewRate=None):
        self._telescope.Asynchronous = 0
        self._telescope.Jog(offset / 60.0, 'West')
        self._telescope.Asynchronous = 1

    @com
    def move_north(self, offset, slewRate=None):
        self._telescope.Asynchronous = 0
        self._telescope.Jog(offset / 60.0, 'North')
        self._telescope.Asynchronous = 1

    @com
    def move_south(self, offset, slewRate=None):
        self._telescope.Asynchronous = 0
        self._telescope.Jog(offset / 60.0, 'South')
        self._telescope.Asynchronous = 1

    @lock
    def sync_ra_dec(self, ra, dec):
        self._telescope.Sync(ra, dec, "chimera")
        self.sync_complete(ra, dec)

    def control(self):
        try:
            if not self.is_slewing() and self.is_tracking():
                try:
                    self._validateAltAz(self.get_position_alt_az())
                except ObjectTooLowException as msg:
                    self.log.exception(msg)
                    self.stop_tracking()
                    self.log.debug('Tracking stopped.')
                    self.tracking_stopped(self.get_position_ra_dec(), TelescopeStatus.OBJECT_TOO_LOW)
        except ChimeraException as msg:
            # If telescope is not connected (parked) it returns a ChimeraException which can be ignored.
            # self.log.exception(msg)
            pass
        return True
