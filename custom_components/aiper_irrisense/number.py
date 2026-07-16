"""Number platform for the Aiper Irrisense 2.

Exposes the two weather-skip thresholds from the watering-setting slot as
adjustable numbers: the rain amount and the wind speed above which the device
defers a scheduled run. Both write back through ``updateWateringSetting`` (full
setting object merged by the coordinator).

Wire units (the raw float the device stores) are fixed regardless of the user's
locale; the Aiper app converts them for display:

* ``rainAmount``  -> **inches** (app gauge 0.1-1.0 in), shown as mm for metric
  users. Cross-checked against a real device: raw 0.5 renders as ~13 mm.
* ``windSpeed``   -> **metres/second** (app slider 2.2-20.1 m/s), shown as km/h
  or mph. Cross-checked: raw 8.2 renders as ~30 km/h (8.2 * 3.6 = 29.5).

So each entity declares its native unit and a matching device class; Home
Assistant then converts to the viewer's unit system automatically, and
``async_set_native_value`` always receives (and writes) the native wire value.
"""
from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IrrisenseCoordinator
from .entity import IrrisenseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrisenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[NumberEntity] = []
    for dev in coordinator.devices:
        sn = dev.get("sn")
        if not sn:
            continue
        entities.extend(
            [
                RainThresholdNumber(coordinator, sn),
                WindThresholdNumber(coordinator, sn),
            ]
        )
    async_add_entities(entities)


class _SettingNumber(IrrisenseEntity, NumberEntity):
    """Base for a single float key in the ``setting`` slot."""

    _setting_key: str = ""
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        setting = self._slot.get("setting")
        if not isinstance(setting, dict):
            return None
        val = setting.get(self._setting_key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_watering_setting(
            self._sn, {self._setting_key: value}
        )


class RainThresholdNumber(_SettingNumber):
    """Rain amount above which a scheduled run is skipped (``rainAmount``)."""

    _setting_key = "rainAmount"
    _attr_device_class = NumberDeviceClass.PRECIPITATION
    _attr_native_unit_of_measurement = UnitOfPrecipitationDepth.INCHES
    _attr_native_min_value = 0.1
    _attr_native_max_value = 1.0
    _attr_native_step = 0.1
    _attr_icon = "mdi:weather-rainy"
    _attr_translation_key = "rain_threshold"

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "rain_threshold")


class WindThresholdNumber(_SettingNumber):
    """Wind speed above which a scheduled run is skipped (``windSpeed``)."""

    _setting_key = "windSpeed"
    _attr_device_class = NumberDeviceClass.WIND_SPEED
    _attr_native_unit_of_measurement = UnitOfSpeed.METERS_PER_SECOND
    _attr_native_min_value = 2.2
    _attr_native_max_value = 20.1
    _attr_native_step = 0.1
    _attr_icon = "mdi:weather-windy"
    _attr_translation_key = "wind_threshold"

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "wind_threshold")
