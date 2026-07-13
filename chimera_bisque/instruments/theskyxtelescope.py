import threading
import time

from chimera.core.lock import lock
from chimera.instruments.telescope import TelescopeBase
from chimera.interfaces.telescope import TelescopeStatus
from chimera.util.coord import Coord
from chimera.util.position import Position, System

from theskyxdriver import TheSkyXDriver, TheSkyXConnectionError, TheSkyXCommandError


class TheSkyXTelescope(TelescopeBase):
    __config__ = {
        "skyx_host": "localhost",
        "skyx_port": 3040,
        "max_slew_time_sec": 300,
        "poll_interval_sec": 0.1,
        "min_altitude": -90,
    }

    def __init__(self):
        super().__init__()
        self._driver: TheSkyXDriver | None = None
        self._abort = threading.Event()
        self._tracking = False
        self._parked = False

    @lock
    def __start__(self):
        self.log.info("Starting TheSkyX telescope instrument")

        try:
            self._driver = TheSkyXDriver(
                self.log,
                host=self["skyx_host"],
                port=int(self["skyx_port"]),
            )
            self._driver.connect()
            self.log.info("Connected TheSkyX successfully")

        except TheSkyXConnectionError as e:
            self.log.error(f"Failed to connect to TheSkyX: {e}")
            raise

    @lock
    def __stop__(self):
        self.log.info("Stopping TheSkyX telescope instrument")
        if self._driver:
            try:
                self._driver.disconnect()
            except Exception as e:
                self.log.warning(f"Error disconnecting from TheSkyX: {e}")

    @lock
    def slew_to_ra_dec(self, ra: float, dec: float, epoch: float = 2000) -> None:
        """Slew telescope to target RA/Dec coordinates.

        Args:
            ra: Right Ascension in hours (0-24)
            dec: Declination in degrees (-90 to +90)
            epoch: Epoch of coordinates (default: J2000)

        Raises:
            ObjectTooLowException: If target is below minimum altitude
            RuntimeError: If slew fails or times out
        """
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        if epoch != 2000.0:
            raise NotImplementedError(f"Only J2000 epoch is supported. Got: {epoch}")

        self._validate_ra_dec(ra, dec)
        self.slew_begin(ra, dec)
        self._abort.clear()

        try:
            self._driver.slew_to_ra_dec(ra, dec)

            # Poll for slew completion
            start_time = time.time()
            max_slew_time_sec = float(self["max_slew_time_sec"])
            poll_interval_sec = float(self["poll_interval_sec"])

            while True:
                if self._abort.is_set():
                    self._driver.abort_slew()
                    self.slew_complete(ra, dec, TelescopeStatus.ABORTED)
                    return

                elapsed = time.time() - start_time
                if elapsed > max_slew_time_sec:
                    self._driver.abort_slew()
                    raise RuntimeError(
                        f"Slew timeout: took longer than {max_slew_time_sec}s"
                    )

                if not self._driver.is_slewing():
                    self.slew_complete(ra, dec, TelescopeStatus.OK)
                    return

                time.sleep(poll_interval_sec)

        except TheSkyXCommandError as e:
            self.slew_complete(ra, dec, TelescopeStatus.ERROR)
            raise RuntimeError(f"Slew failed: {e}")

    @lock
    def slew_to_alt_az(self, alt: float, az: float) -> None:
        """Slew telescope to target Alt/Az coordinates.

        This is implemented by converting Alt/Az to RA/Dec and calling slew_to_ra_dec.

        Args:
            alt: Altitude in degrees (0-90)
            az: Azimuth in degrees (0-360)

        Raises:
            ObjectTooLowException: If altitude is below minimum
            RuntimeError: If conversion or slew fails
        """
        self._validate_alt_az(alt, az)

        site = self.site()
        ra, dec = site.alt_az_to_ra_dec(alt, az)

        self.slew_to_ra_dec(ra, dec)

    def abort_slew(self) -> None:
        """Abort any in-progress slew."""
        self._abort.set()

    def is_slewing(self) -> bool:
        """Check if telescope is currently slewing."""
        if self._driver is None:
            return False
        try:
            return self._driver.is_slewing()
        except TheSkyXConnectionError:
            return False

    def get_position_ra_dec(self) -> tuple[float, float]:
        """Get current telescope RA/Dec position.

        Returns:
            (ra_hours, dec_degrees): Current position
        """
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")
        try:
            return self._driver.get_ra_dec()
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Failed to get position: {e}")

    def get_position_alt_az(self) -> tuple[float, float]:
        """Get current telescope Alt/Az position.

        Returns:
            (alt_degrees, az_degrees): Current altitude and azimuth
        """
        ra, dec = self.get_position_ra_dec()
        site = self.site()
        alt, az = site.ra_dec_to_alt_az(ra, dec)
        return alt, az

    @lock
    def sync_ra_dec(self, ra: float, dec: float, epoch: float = 2000) -> None:
        """Sync telescope to current position (calibration).

        This tells the telescope "I'm at this RA/Dec right now" to correct
        tracking or alignment errors.

        Args:
            ra: Right Ascension in hours (0-24)
            dec: Declination in degrees (-90 to +90)
            epoch: Epoch of coordinates (default: J2000)
        """
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        if epoch != 2000.0:
            raise NotImplementedError(f"Only J2000 epoch is supported. Got: {epoch}")

        try:
            self._driver.sync_ra_dec(ra, dec)
            self.sync_complete(ra, dec)
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Sync failed: {e}")

    @lock
    def move_east(self, offset: float, rate=None) -> None:
        ra, dec = self.get_position_ra_dec()
        new_ra = ra + Coord.from_as(offset).to_h()
        self.slew_to_ra_dec(new_ra, dec)

    @lock
    def move_west(self, offset: float, rate=None) -> None:
        ra, dec = self.get_position_ra_dec()
        new_ra = ra - Coord.from_as(offset).to_h()
        self.slew_to_ra_dec(new_ra, dec)

    @lock
    def move_north(self, offset: float, rate=None) -> None:
        ra, dec = self.get_position_ra_dec()
        new_dec = dec + Coord.from_as(offset).to_d()
        self.slew_to_ra_dec(ra, new_dec)

    @lock
    def move_south(self, offset: float, rate=None) -> None:
        ra, dec = self.get_position_ra_dec()
        new_dec = dec - Coord.from_as(offset).to_d()
        self.slew_to_ra_dec(ra, new_dec)

    @lock
    def start_tracking(self) -> None:
        """Start telescope tracking."""
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        try:
            self._driver.start_tracking()
            self._tracking = True
            self.tracking_started()
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Failed to start tracking: {e}")

    @lock
    def stop_tracking(self) -> None:
        """Stop telescope tracking."""
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        try:
            self._driver.stop_tracking()
            self._tracking = False
            self.tracking_stopped(TelescopeStatus.OK)
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Failed to stop tracking: {e}")

    def is_tracking(self) -> bool:
        """Check if telescope is tracking.

        Returns:
            True if tracking, False otherwise
        """
        if self._driver is None:
            return False

        return self._driver.is_tracking()

    @lock
    def set_park_position(self, position: Position):
        raise NotImplementedError("Parking position is to be set manually in TheSkyX GUI")


    @lock
    def park(self) -> None:
        """Park the telescope."""
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        try:
            self._driver.park()
            self._parked = True
            self.park_complete(TelescopeStatus.OK)
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Failed to park telescope: {e}")

    @lock
    def unpark(self) -> None:
        """Unpark the telescope."""
        if self._driver is None:
            raise RuntimeError("Telescope not initialized")

        try:
            self._driver.unpark()
            self._parked = False
        except TheSkyXCommandError as e:
            raise RuntimeError(f"Failed to unpark telescope: {e}")

    def is_parked(self) -> bool:
        """Check if telescope is parked.

        Returns:
            True if parked, False otherwise
        """
        if self._driver is None:
            return False

        return self._driver.is_parked()
