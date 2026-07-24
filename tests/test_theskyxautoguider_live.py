# SPDX-FileCopyrightText: 2026-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live integration tests for the TheSkyX autoguider against a real TheSkyX.

Skipped unless ``THESKYX_TEST_URL`` (``host:port``) points at a running
TheSkyX with an autoguider camera configured (the built-in Camera Simulator
works) and a calibrated autoguider, e.g.::

    THESKYX_TEST_URL=localhost:3040 uv run pytest tests/test_theskyxautoguider_live.py -v

The full guide cycle (take photo, auto-select star, autoguide, abort) moves
the autoguider state machine, so it is additionally gated behind
``THESKYX_TEST_DESTRUCTIVE=1``.
"""

import logging
import os

import pytest

from chimera_bisque.instruments.theskyxautoguiderdriver import (
    CameraState,
    TheSkyXAutoguiderDriver,
)

_URL = os.environ.get("THESKYX_TEST_URL")
_DESTRUCTIVE = os.environ.get("THESKYX_TEST_DESTRUCTIVE")

pytestmark = pytest.mark.skipif(
    not _URL, reason="THESKYX_TEST_URL not set; skipping live TheSkyX tests"
)


@pytest.fixture
def driver():
    host, _, port = _URL.partition(":")
    driver = TheSkyXAutoguiderDriver(
        logging.getLogger("test"), host=host, port=int(port or 3040)
    )
    driver.connect()
    yield driver
    driver.disconnect()


def test_connect_and_state(driver):
    assert isinstance(driver.get_state(), CameraState)


def test_take_image_and_select_star(driver):
    driver.take_image(1.0)
    x, y = driver.select_guide_star()
    assert x > 0 and y > 0


@pytest.mark.skipif(
    not _DESTRUCTIVE,
    reason="THESKYX_TEST_DESTRUCTIVE not set; skipping guide cycle",
)
def test_full_guide_cycle(driver):
    driver.take_image(1.0)
    driver.select_guide_star()
    driver.start_autoguide(1.0)
    try:
        assert driver.get_state() in (
            CameraState.AUTOGUIDE,
            CameraState.MOVE_GUIDE_STAR,
        )
    finally:
        driver.abort()
    assert driver.get_state() == CameraState.IDLE
