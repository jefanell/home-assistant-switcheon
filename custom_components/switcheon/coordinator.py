"""Push-driven coordinator.

Box state arrives over SwitcheOn's SignalR connection. The account is read over
REST only at startup, after each reconnect, and when a box goes quiet long enough
that it may be offline (the server doesn't push online/offline changes).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import SwitcheOnApi, SwitcheOnApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# The server marks a box offline once it has gone more than 11 minutes without a
# check-in, sweeping once a minute. Wait a little past that before asking.
OFFLINE_AFTER = timedelta(minutes=13)
OFFLINE_CHECK_INTERVAL = timedelta(minutes=1)
# Never verify more often than this, e.g. while the API is unreachable.
VERIFY_THROTTLE = timedelta(minutes=5)
# A reconnect within this long of a REST read doesn't need another one.
RECONNECT_REFRESH_GRACE = timedelta(minutes=1)
RECONNECT_DELAY = 5


class SwitcheOnCoordinator(DataUpdateCoordinator[list[dict]]):
    """Hold account box state, updated from live check-ins."""

    def __init__(self, hass: HomeAssistant, api: SwitcheOnApi) -> None:
        self.api = api
        self.account = api.account
        self._entry: ConfigEntry | None = None
        self._websocket_task: asyncio.Task | None = None
        self._last_checkin: dict[str, datetime] = {}
        self._last_refresh: datetime | None = None
        self._next_verify = dt_util.utcnow()
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=None,
        )

    async def _async_update_data(self) -> list[dict]:
        try:
            boxes = await self.api.async_get_boxes()
        except SwitcheOnApiError as err:
            raise UpdateFailed(str(err)) from err
        now = dt_util.utcnow()
        self._last_refresh = now
        for box in boxes:
            box_id = box.get("boxIdBin")
            last = self._last_checkin.get(box_id)
            # The server's word that a box is online counts as a sighting, so a box
            # it reports online gets a full window before we question it again.
            if box.get("online") and (last is None or now - last > OFFLINE_AFTER):
                self._last_checkin[box_id] = now
        return boxes

    @callback
    def async_start(self, entry: ConfigEntry) -> None:
        """Start the live connection and offline watchdog for the life of the entry."""
        self._entry = entry
        self._start_websocket()
        entry.async_on_unload(
            async_track_time_interval(self.hass, self._async_check_offline, OFFLINE_CHECK_INTERVAL)
        )

    def _start_websocket(self) -> None:
        if self._websocket_task is not None and not self._websocket_task.done():
            return
        self._websocket_task = self._entry.async_create_background_task(
            self.hass, self._websocket_loop(), f"{DOMAIN} live updates"
        )

    def _restart_websocket(self) -> None:
        if self._websocket_task is not None:
            self._websocket_task.cancel()
            self._websocket_task = None
        self._start_websocket()

    async def _websocket_loop(self) -> None:
        while True:
            try:
                await self.api.async_listen(self._apply_live_update, self._on_connected)
                _LOGGER.debug("SwitcheOn live connection closed; reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception as err:  # reconnect on transient API/network failures
                _LOGGER.warning("SwitcheOn live connection lost; reconnecting: %s", err)
            await asyncio.sleep(RECONNECT_DELAY)

    @callback
    def _on_connected(self) -> None:
        """Catch up on anything missed while the connection was down."""
        if self._last_refresh is not None and dt_util.utcnow() - self._last_refresh < RECONNECT_REFRESH_GRACE:
            return
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_check_offline(self, _now=None) -> None:
        """Confirm with the server when an online box has stopped checking in."""
        now = dt_util.utcnow()
        quiet = {
            box.get("boxIdBin")
            for box in self.data or []
            if box.get("online") and now - self._last_checkin.get(box.get("boxIdBin"), now) > OFFLINE_AFTER
        }
        if not quiet or now < self._next_verify:
            return
        self._next_verify = now + VERIFY_THROTTLE
        await self.async_refresh()
        if not self.last_update_success:
            return
        # The server only keeps a box online while it checks in, and every check-in
        # is pushed. If it still says online, our connection has stopped receiving.
        if any(box.get("online") and box.get("boxIdBin") in quiet for box in self.data):
            _LOGGER.warning(
                "SwitcheOn reports boxes online but no check-ins have arrived; reconnecting live updates"
            )
            self._restart_websocket()

    @callback
    def _apply_live_update(self, update: dict) -> None:
        """Merge a check-in or request echo into the matching box."""
        box_id = update.get("BoxIdBin")
        boxes = [dict(box) for box in (self.data or [])]
        for box in boxes:
            if box.get("boxIdBin") != box_id:
                continue
            if update.get("req") is not None:
                # Request echo: someone asked the box to switch.
                box["pendingStatus"] = update["req"]
            else:
                # Check-in: the box is talking to the server right now.
                box["online"] = 1
                self._last_checkin[box_id] = dt_util.utcnow()
                if update.get("stat") is not None:
                    box["currentStatus"] = update["stat"]
                    box["pendingStatus"] = None
                if update.get("temp") is not None:
                    box["temp"] = update["temp"]
                if update.get("excl") is not None:
                    box["channelsExclusive"] = update["excl"]
            self.async_set_updated_data(boxes)
            return

    def set_pending_status(self, box_id: str, status: int) -> None:
        """Reflect an accepted channel request while the box applies it."""
        boxes = [dict(box) for box in (self.data or [])]
        for box in boxes:
            if box.get("boxIdText") == box_id:
                box["pendingStatus"] = status
                self.async_set_updated_data(boxes)
                return
