# SPDX-FileCopyrightText: 2025-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live integration tests for the TheSkyX driver against a real TheSkyX.

Skipped unless ``THESKYX_TEST_URL`` (``host:port``) points at a running
TheSkyX with a Telescope Mount Simulator connected, e.g.::

    THESKYX_TEST_URL=localhost:13040 uv run pytest tests/test_theskyx_live.py -v

The core tests below (connect/disconnect, position queries, asynchronous slews
with polling, arrival, sync, and the not-connected error path) were validated
against the TheSkyX "Telescope Mount Simulator".

Two operations are *not* covered by default because they misbehave on that
simulator (they work on real mounts):

* ``SetTracking`` never returns (the call blocks TheSkyX server-side).
* After ``Abort()`` (and ``Park()``), the next ``IsSlewComplete`` read hangs,
  which then wedges TheSkyX's single-script command queue.

Because those wedge the simulator, the abort/park tests are gated behind
``THESKYX_TEST_DESTRUCTIVE=1`` so the default run stays clean. Run them only
against real hardware::

    THESKYX_TEST_URL=host:3040 THESKYX_TEST_DESTRUCTIVE=1 uv run pytest ...
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
_DESTRUCTIVE = os.environ.get("THESKYX_TEST_DESTRUCTIVE")

pytestmark = pytest.mark.skipif(
    not _URL, reason="set THESKYX_TEST_URL=host:port to run live TheSkyX tests"
)

SLEW_TIMEOUT = 60.0
POLL = 0.5


def _make_driver(timeout=8.0):
    host, _, port = _URL.partition(":")
    return TheSkyXDriver(
        logging.getLogger("live"), host=host, port=int(port or 3040), timeout=timeout
    )


def _wait_slew_done(driver, timeout=SLEW_TIMEOUT):
    start = time.time()
    while driver.is_slewing():
        if time.time() - start > timeout:
            driver.abort_slew()
            raise AssertionError("slew did not finish within timeout")
        time.sleep(POLL)


@pytest.fixture(scope="module")
def driver():
    drv = _make_driver()
    drv.connect()
    assert drv._is_connected
    yield drv
    try:
        drv.disconnect()
    except Exception:
        pass


def test_get_position(driver):
    ra, dec = driver.get_ra_dec()
    assert 0.0 <= ra < 24.0
    assert -90.0 <= dec <= 90.0


def test_initial_not_slewing(driver):
    assert driver.is_slewing() is False


def test_sync_is_noop_at_current_position(driver):
    ra0, dec0 = driver.get_ra_dec()
    driver.sync_ra_dec(ra0, dec0)
    ra1, dec1 = driver.get_ra_dec()
    assert abs(ra1 - ra0) < 0.05
    assert abs(dec1 - dec0) < 0.5


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


def test_back_to_back_slews(driver):
    ra0, dec0 = driver.get_ra_dec()
    driver.slew_to_ra_dec(ra0, dec0 - 1.0)
    _wait_slew_done(driver)
    # a fresh slew immediately after completion must be accepted
    driver.slew_to_ra_dec(ra0, dec0)
    _wait_slew_done(driver)
    ra1, dec1 = driver.get_ra_dec()
    assert abs(ra1 - ra0) < 0.05
    assert abs(dec1 - dec0) < 0.5


def test_get_position_requires_connection():
    drv = _make_driver()
    with pytest.raises(TheSkyXConnectionError):
        drv.get_ra_dec()


@pytest.mark.skipif(
    not _DESTRUCTIVE,
    reason="set THESKYX_TEST_DESTRUCTIVE=1 (real hardware only; wedges the sim)",
)
def test_abort_slew(driver):
    ra0, dec0 = driver.get_ra_dec()
    driver.slew_to_ra_dec(ra0 + 0.3, dec0 + 3.0)
    time.sleep(0.5)
    assert driver.is_slewing() is True
    driver.abort_slew()
    start = time.time()
    while driver.is_slewing():
        assert time.time() - start < 15, "mount still slewing after abort"
        time.sleep(POLL)


@pytest.mark.skipif(
    not _DESTRUCTIVE,
    reason="set THESKYX_TEST_DESTRUCTIVE=1 (real hardware only; wedges the sim)",
)
def test_park_and_unpark(driver):
    driver.set_park_position()
    driver.park()
    start = time.time()
    while driver.is_slewing():
        assert time.time() - start < SLEW_TIMEOUT
        time.sleep(POLL)
    assert driver.is_parked() is True
    driver.unpark()
    assert driver.is_parked() is False
