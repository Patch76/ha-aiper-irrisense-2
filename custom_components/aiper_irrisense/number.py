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
from homeassistant.const import UnitOfLength, UnitOfTime
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DEPTH_MM_MAX,
    DEPTH_MM_MIN,
    POINT_TIME_MAX,
    POINT_TIME_MIN,
)
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
                DepthNumber(coordinator, sn),
                DurationNumber(coordinator, sn),
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


class _DoseNumberBase(IrrisenseEntity, RestoreEntity, NumberEntity):
    """Free dose/duration input that feeds the shared dose selection.

    Holds its own last-entered value (restored across restarts). Writing it
    also updates the coordinator's dose selection as a label the Start button
    parses, so the Number and the Dose select share one 'what will Start use'
    value on a last-writer-wins basis.
    """

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str, key: str) -> None:
        super().__init__(coordinator, sn, key)
        self._value: float | None = None

    @property
    def native_value(self) -> float | None:
        return self._value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._value = float(last.state)
            except (TypeError, ValueError):
                self._value = None

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.coordinator.set_dose_selection(self._sn, self._label_for(value))
        self.async_write_ha_state()

    def _label_for(self, value: float) -> str:
        raise NotImplementedError


class DepthNumber(_DoseNumberBase):
    """Watering depth in mm (Area/Line zones), 3..23 mm."""

    _attr_translation_key = "watering_depth"
    _attr_icon = "mdi:water"
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_min_value = float(DEPTH_MM_MIN)
    _attr_native_max_value = float(DEPTH_MM_MAX)

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "watering_depth")

    def _label_for(self, value: float) -> str:
        return f"{int(round(value))} mm"


class DurationNumber(_DoseNumberBase):
    """Watering duration in minutes (Point zones), 1..150 min."""

    _attr_translation_key = "watering_duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = float(POINT_TIME_MIN)
    _attr_native_max_value = float(POINT_TIME_MAX)

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "watering_duration")

    def _label_for(self, value: float) -> str:
        return f"{int(round(value))} min"
