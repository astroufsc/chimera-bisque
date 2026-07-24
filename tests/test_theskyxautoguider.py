# SPDX-FileCopyrightText: 2026-present William Schoenell <wschoenell@gmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the TheSkyX autoguider driver and instrument against a fake
TheSkyX server that emulates the ccdsoftAutoguider scripting object."""

import logging
import socketserver
import threading

import pytest
from chimera.interfaces.autoguider import GuiderStatus

from chimera_bisque.instruments.theskyxautoguider import TheSkyXAutoguider
from chimera_bisque.instruments.theskyxautoguiderdriver import (
    CameraState,
    TheSkyXAutoguiderDriver,
    TheSkyXNoStarError,
)


class _FakeAutoguiderHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(4096).decode("utf-8", errors="ignore")
        self.server.scripts.append(data)
        self.request.sendall(self._respond(data).encode("utf-8"))
        try:
            self.request.recv(1)
        except OSError:
            pass

    def _respond(self, command: str) -> str:
        server = self.server
        if "Autoguide()" in command:
            server.state = CameraState.AUTOGUIDE
            return "OK"
        if "Abort()" in command:
            server.state = CameraState.IDLE
            return "OK"
        if "TakeImage()" in command:
            return "OK"
        if "IsExposureComplete" in command:
            return "1"
        if "ShowInventory" in command:
            return "NOSTAR" if server.no_star else "512.5 384.25"
        if "Out = ccdsoftAutoguider.State" in command:
            return str(int(server.state))
        if "String(ccdsoftAutoguider.GuideStarX)" in command:
            return "512.5 384.25"
        return "OK"


@pytest.fixture
def skyx_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FakeAutoguiderHandler)
    server.scripts = []
    server.state = CameraState.IDLE
    server.no_star = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def driver(skyx_server):
    host, port = skyx_server.server_address
    driver = TheSkyXAutoguiderDriver(logging.getLogger("test"), host=host, port=port)
    driver.connect()
    return driver


@pytest.fixture
def guider(manager, skyx_server):
    host, port = skyx_server.server_address
    yield manager.add_class(
        TheSkyXAutoguider,
        "skyx",
        config={"skyx_host": host, "skyx_port": port, "exptime": 0.1},
    )


# driver


def test_driver_take_image_and_select_star(driver):
    driver.take_image(0.1)
    x, y = driver.select_guide_star()
    assert (x, y) == (512.5, 384.25)


def test_driver_select_star_no_star(driver, skyx_server):
    skyx_server.no_star = True
    driver.take_image(0.1)
    with pytest.raises(TheSkyXNoStarError):
        driver.select_guide_star()


def test_driver_autoguide_cycle(driver):
    assert driver.get_state() == CameraState.IDLE
    driver.start_autoguide(0.1)
    assert driver.get_state() == CameraState.AUTOGUIDE
    driver.abort()
    assert driver.get_state() == CameraState.IDLE


# instrument


def test_start_and_stop_guiding(guider, skyx_server):
    events = []
    guider.star_acquired += lambda pos: events.append(("star_acquired", pos))
    guider.guide_start += lambda pos: events.append(("guide_start", pos))
    guider.guide_stop += lambda state, msg=None: events.append(("guide_stop", state))

    assert guider.get_status() == GuiderStatus.OFF
    guider.start_guiding(False, True)

    assert guider.is_guiding() is True
    assert guider.get_status() == GuiderStatus.GUIDING

    guider.stop_guiding()
    assert guider.is_guiding() is False

    names = [name for name, _ in events]
    assert names == ["star_acquired", "guide_start", "guide_stop"]
    assert events[0][1] == [512.5, 384.25]

    # the whole UI sequence went over the wire: photo, inventory, autoguide
    joined = "\n".join(skyx_server.scripts)
    assert "TakeImage()" in joined
    assert "ShowInventory()" in joined
    assert "Autoguide()" in joined


def test_find_star_not_found_raises(guider, skyx_server):
    # remote exceptions surface as generic Exception with the original
    # exception type in the message
    skyx_server.no_star = True
    with pytest.raises(Exception, match="StarNotFoundException"):
        guider.find_star()


def test_dither_shifts_guide_star_and_restarts(guider, skyx_server):
    guider.start_guiding(False, True)

    events = []
    guider.dither_complete += lambda offset, status: events.append((offset, status))

    guider.dither(2.0, False, True)
    assert guider.is_guiding() is True
    assert len(events) == 1
    offset, status = events[0]
    assert status == GuiderStatus.OK
    assert all(abs(v) <= 2.0 for v in offset)

    # dither = abort + move lock position + autoguide again
    joined = "\n".join(skyx_server.scripts)
    assert "GuideStarX = " in joined
    assert joined.count("Autoguide()") == 2


def test_dither_requires_guiding(guider):
    with pytest.raises(Exception, match="not guiding"):
        guider.dither(2.0, False, False)
