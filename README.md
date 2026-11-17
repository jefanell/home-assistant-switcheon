# SwitcheOn Home Assistant integration

This custom integration reads the SwitcheOn account API and exposes a switch
for each channel, plus temperature and online state. Channel switches send
commands through the SwitcheOn API; switch state follows the box's reported
state and changes after the box checks in. Live check-ins arrive over SwitcheOn's
SignalR websocket.

## Install

Copy the `custom_components/switcheon` folder into the `custom_components`
folder in your Home Assistant configuration directory. Restart Home Assistant,
then go to **Settings → Devices & Services → Add Integration**, search for
**SwitcheOn**, and enter the email and password used by the SwitcheOn phone app.

The integration does not poll. Box state, temperature and online status come
from SwitcheOn's SignalR websocket as each box checks in. The account is read
over REST only at startup, after the websocket reconnects, and when an online
box has been silent for 13 minutes (the server marks a box offline after 11),
to confirm it went offline. If the server still reports such a box online, the
websocket has stopped delivering updates and is reconnected. SwitcheOn login
tokens last eight hours; the integration renews them before they expire. Credentials are stored by Home
Assistant in the config entry.

## Entities

For each box present when the integration is first set up, it creates:

- `switch.*`: one toggle per channel, named from the box's channel names when
  available. The reported state reflects the box, and `command_pending`
  indicates a queued request awaiting the next check-in.
- `sensor.*_temperature`: device temperature in the unit configured on the box.
- `binary_sensor.*_online`: API-reported online state.

## Notes

- The API uses `currentStatus` as reported state; `pendingStatus` is only a
  request waiting to be applied. The integration converts the bitmask to
  separate channel switches.
- SwitcheOn does not push online/offline changes. A check-in marks a box
  online immediately; going offline is confirmed with one account read after
  13 minutes without a check-in.
- Boxes added to the account after integration setup require reloading the
  integration to create their entities.
- API reference: https://github.com/SeanMollet/SwitcheOn-API

## Local account/API diagnostic

To compare the account lookup from your Mac, run `python3 local_api_check.py`
from this project folder and open the local page it launches. The page submits
credentials only to the SwitcheOn HTTPS API. The server binds to `127.0.0.1`,
does not log form contents, and displays only the returned account and box
identifiers. It does not save credentials or API tokens. Stop it with Ctrl+C.
