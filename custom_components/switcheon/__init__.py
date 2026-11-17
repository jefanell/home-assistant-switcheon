"""SwitcheOn integration setup."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SwitcheOnApi
from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN
from .coordinator import SwitcheOnCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a SwitcheOn account."""
    api = SwitcheOnApi(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    coordinator = SwitcheOnCoordinator(hass, api)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]
    )
    coordinator.async_start(entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a SwitcheOn account."""
    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry, [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]
    ):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
