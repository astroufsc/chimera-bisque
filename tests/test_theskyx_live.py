# SPDX-FileCopyrightText: 2025-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live integration tests for the TheSkyX driver against a real TheSkyX.

Skipped unless ``THESKYX_TEST_URL`` (``host:port``) points at a running
TheSkyX with a Telescope Mount Simulator connected, e.g.::

    THESKYX_TEST_URL=localhost:13040 uv run pytest tests/test_theskyx_live.py -v

These exercise the whole driver interface: connect/disconnect, position
queries, asynchronous slews with polling, abort, sync, tracking and parking.
All slews are small offsets from the current pointing so the mount stays above
the horizon.
"""

import logging
import os
import time

import pytest

from chimera_bisque.instruments.theskyxdriver import (
    TheSkyXConnectionError,
    TheSkyXDriver,
)

_URL = os.environ.get("THESKYX_TEST_URL")

pytestmark = pytest.mark.skipif(
    not _URL, reason="set THESKYX_TEST_URL=host:port to run live TheSkyX tests"
)

SLEW_TIMEOUT = 60.0
POLL = 0.5


def _wait_slew_done(driver, timeout=SLEW_TIMEOUT):
    start = time.time()
    while driver.is_slewing():
        if time.time() - start > timeout:
            driver.abort_slew()
            raise AssertionError("slew did not finish within timeout")
        time.sleep(POLL)


@pytest.fixture(scope="module")
def driver():
    host, _, port = _URL.partition(":")
    drv = TheSkyXDriver(logging.getLogger("live"), host=host, port=int(port or 3040))
    drv.connect()
    assert drv._is_connected
    yield drv
    # leave the mount tracking-off and disconnected cleanly
    try:
        drv.stop_tracking()
    except Exception:
        pass
    drv.disconnect()
    assert not drv._is_connected


def test_get_position(driver):
    ra, dec = driver.get_ra_dec()
    assert 0.0 <= ra < 24.0
    assert -90.0 <= dec <= 90.0


def test_initial_not_slewing(driver):
    assert driver.is_slewing() is False


def test_tracking_toggle(driver):
    driver.start_tracking()
    assert driver.is_tracking() is True
    driver.stop_tracking()
    assert driver.is_tracking() is False


def test_async_slew_and_arrival(driver):
    ra0, dec0 = driver.get_ra_dec()
    target_ra = ra0 + 0.1  # ~1.5 deg
    target_dec = dec0 + 1.0
    driver.slew_to_ra_dec(target_ra, target_dec)
    _wait_slew_done(driver)
    ra1, dec1 = driver.get_ra_dec()
    assert abs(ra1 - target_ra) < 0.05
    assert abs(dec1 - target_dec) < 0.5
    # put it back
    driver.slew_to_ra_dec(ra0, dec0)
    _wait_slew_done(driver)


def test_abort_slew(driver):
    ra0, dec0 = driver.get_ra_dec()
    driver.slew_to_ra_dec(ra0 + 0.3, dec0 + 3.0)
    time.sleep(0.5)
    driver.abort_slew()
    # after abort the mount must settle (not slewing) shortly
    start = time.time()
    while driver.is_slewing():
        assert time.time() - start < 10, "mount still slewing after abort"
        time.sleep(POLL)
    # return to start for a clean state
    driver.slew_to_ra_dec(ra0, dec0)
    _wait_slew_done(driver)


def test_sync_is_noop_at_current_position(driver):
    ra0, dec0 = driver.get_ra_dec()
    driver.sync_ra_dec(ra0, dec0)
    ra1, dec1 = driver.get_ra_dec()
    assert abs(ra1 - ra0) < 0.05
    assert abs(dec1 - dec0) < 0.5


def test_park_and_unpark(driver):
    driver.set_park_position()
    driver.park()
    # parking may involve a short slew; wait for it to settle
    start = time.time()
    while driver.is_slewing():
        assert time.time() - start < SLEW_TIMEOUT
        time.sleep(POLL)
    assert driver.is_parked() is True
    driver.unpark()
    assert driver.is_parked() is False


def test_get_position_requires_connection():
    host, _, port = _URL.partition(":")
    drv = TheSkyXDriver(logging.getLogger("live"), host=host, port=int(port or 3040))
    with pytest.raises(TheSkyXConnectionError):
        drv.get_ra_dec()
