"""Small async client for the read-only parts of the SwitcheOn API."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from aiohttp import ClientError, ClientSession, WSMsgType

from .const import API_BASE


WS_URL = "https://www.switcheon.com/api/userhub"
# Tokens last eight hours; renew a little early so a reconnect never registers with
# an expired one.
TOKEN_RENEW_SECONDS = 8 * 3600 - 10 * 60
# The server drops a connection it hasn't heard from in 30 seconds, even while it is
# sending updates, so ping on a fixed schedule rather than only when idle.
PING_INTERVAL = 15
RECORD_SEPARATOR = "\x1e"
PING = '{"type":6}' + RECORD_SEPARATOR


class SwitcheOnApiError(Exception):
    """API request failed."""


class SwitcheOnApi:
    """API client; credentials are kept in memory and login is renewed as needed."""

    def __init__(self, session: ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._user_id: str | None = None
        self._token: str | None = None
        self._token_renew_at = 0.0
        self.account: dict[str, Any] = {}

    async def _login(self) -> None:
        try:
            async with self._session.post(
                f"{API_BASE}/User",
                data={"Email": self._email, "UserPassword": self._password},
            ) as response:
                if response.status == 401:
                    raise SwitcheOnApiError("invalid_auth")
                response.raise_for_status()
                result = await response.json()
        except (ClientError, ValueError) as err:
            raise SwitcheOnApiError("cannot_connect") from err
        self._user_id = result["userIdBin"]
        self._token = result["token"]
        self._token_renew_at = time.monotonic() + TOKEN_RENEW_SECONDS

    async def _ensure_login(self) -> None:
        if self._token is None or self._user_id is None or time.monotonic() >= self._token_renew_at:
            await self._login()

    async def async_get_boxes(self) -> list[dict[str, Any]]:
        """Return account boxes, logging in again if the token was rejected."""
        for attempt in range(2):
            await self._ensure_login()
            try:
                async with self._session.get(
                    f"{API_BASE}/User",
                    params={"UserIdBin": self._user_id, "UserSecret": self._token},
                ) as response:
                    if response.status == 404 and attempt == 0:
                        self._token = None
                        continue
                    response.raise_for_status()
                    payload = await response.json()
            except (ClientError, ValueError) as err:
                raise SwitcheOnApiError("cannot_connect") from err
            self.account = {
                "email": payload.get("email"),
                "firstname": payload.get("firstname"),
                "lastname": payload.get("lastname"),
            }
            return payload.get("boxes", [])
        raise SwitcheOnApiError("invalid_auth")

    async def async_set_channel(self, box: dict[str, Any], channel: int, turn_on: bool) -> int:
        """Queue a complete channel state with one bit changed and return it."""
        current = box.get("pendingStatus")
        if current is None:
            current = box.get("currentStatus") or 0
        bit = 1 << channel
        requested = current | bit if turn_on else current & ~bit
        await self._ensure_login()
        try:
            async with self._session.put(
                f"{API_BASE}/req",
                json={
                    "userIdBin": self._user_id,
                    "userSecret": self._token,
                    "boxIdBin": box["boxIdBin"],
                    "requestedStatus": requested,
                },
            ) as response:
                if response.status == 401:
                    self._token = None
                    await self._login()
                    return await self.async_set_channel(box, channel, turn_on)
                response.raise_for_status()
        except (ClientError, ValueError) as err:
            raise SwitcheOnApiError("cannot_connect") from err
        return requested

    async def async_listen(self, on_update, on_connected=None) -> None:
        """Connect to the SignalR hub and forward every box update until it closes.

        Check-ins (``req`` is null) and request echoes (``req`` set) are both
        forwarded; the caller tells them apart. ``on_connected`` is called once
        the registration is accepted.
        """
        await self._ensure_login()
        loop = asyncio.get_running_loop()
        async with self._session.ws_connect(WS_URL, heartbeat=None) as ws:
            await ws.send_str('{"protocol":"json","version":1}' + RECORD_SEPARATOR)
            handshake = await ws.receive(timeout=15)
            if handshake.type != WSMsgType.TEXT or '"error"' in handshake.data:
                raise SwitcheOnApiError("websocket_handshake_failed")
            await ws.send_str(json.dumps({
                "type": 1,
                "invocationId": "1",
                "target": "registerConnectionSecure",
                "arguments": [self._user_id, self._token],
            }) + RECORD_SEPARATOR)
            last_sent = loop.time()
            registered = False
            while True:
                if loop.time() - last_sent >= PING_INTERVAL:
                    await ws.send_str(PING)
                    last_sent = loop.time()
                try:
                    message = await ws.receive(timeout=PING_INTERVAL - (loop.time() - last_sent))
                except asyncio.TimeoutError:
                    continue
                if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                    return
                if message.type != WSMsgType.TEXT:
                    continue
                for item in message.data.split(RECORD_SEPARATOR):
                    if not item:
                        continue
                    payload = json.loads(item)
                    kind = payload.get("type")
                    if kind == 3 and payload.get("invocationId") == "1":
                        if payload.get("error"):
                            # Most likely a rejected token; log in afresh on the next attempt.
                            self._token = None
                            raise SwitcheOnApiError("websocket_registration_failed")
                        registered = True
                        if on_connected is not None:
                            on_connected()
                    elif kind == 7:
                        return
                    elif kind == 1 and registered and payload.get("target") == "updateFromServer":
                        raw = (payload.get("arguments") or [None])[0]
                        update = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(update, dict) and update.get("BoxIdBin"):
                            on_update(update)
