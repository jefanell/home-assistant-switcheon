"""SwitcheOn connectivity entities."""

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SwitcheOnCoordinator
from .sensor import SwitcheOnBoxEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: SwitcheOnCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(SwitcheOnOnlineSensor(coordinator, box) for box in coordinator.data)


class SwitcheOnOnlineSensor(SwitcheOnBoxEntity, BinarySensorEntity):
    """Whether the box is checking in: set by live check-ins, cleared when the server marks it offline."""

    _attr_has_entity_name = True
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def unique_id(self) -> str:
        return f"{self._box_id}_online"

    @property
    def is_on(self):
        return bool(self.box.get("online"))
