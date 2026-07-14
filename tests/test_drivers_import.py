# SPDX-FileCopyrightText: 2025-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-platform import, instantiation and graceful-degradation tests.

The TheSky 5/6 COM driver requires Windows + pywin32 to actually talk to
hardware, but the plugin must still import and instantiate everywhere so it
can be installed and inspected on non-Windows machines.
"""

import sys

import pytest
from chimera.core.exceptions import ChimeraException

from chimera_bisque.instruments import (
    theskytelescope,
    theskyxtelescope,
)


def test_com_module_degrades_off_windows():
    if sys.platform != "win32":
        assert theskytelescope.Dispatch is None


def test_instantiation_is_hardware_free():
    # constructors must not open any connection
    tel = theskytelescope.TheSkyTelescope()
    assert tel["thesky"] == 6
    assert tel["model"] == "Software Bisque The Sky telescope"

    skyx = theskyxtelescope.TheSkyXTelescope()
    assert skyx["skyx_port"] == 3040


def test_com_decorator_wraps_errors():
    # on non-Windows com_error is Exception, so any raised error is wrapped
    @theskytelescope.com
    def boom():
        raise theskytelescope.com_error("boom")

    with pytest.raises(ChimeraException):
        boom()
