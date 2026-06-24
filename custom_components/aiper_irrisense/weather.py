"""Weather platform for Aiper Irrisense 2 – Apple-WeatherKit-proxied cloud data.

One location-level entity tied to the primary device, driven by HA home
coordinates. Read-only. All field mapping lives in weather_helpers (tested).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.weather import (
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import IrrisenseCoordinator
from .entity import IrrisenseEntity
from .weather_helpers import current_attrs, daily_forecast


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrisenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = coordinator.devices
    if not devices:
        return
    sn = devices[0].get("sn")
    if not sn:
        return
    async_add_entities([IrrisenseWeather(coordinator, sn)])


class IrrisenseWeather(IrrisenseEntity, WeatherEntity):
    """Home weather for the garden, via the Aiper WeatherKit proxy."""

    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.MBAR
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_visibility_unit = UnitOfLength.METERS
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "weather")
        self._attr_name = "Weather"

    @property
    def _current(self) -> dict[str, Any]:
        return ((self.coordinator.weather or {}).get("currentWeather")) or {}

    @property
    def _attrs(self) -> dict[str, Any]:
        return current_attrs(self._current)

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) and bool(self.coordinator.weather)

    @property
    def condition(self) -> str | None:
        return self._attrs["condition"]

    @property
    def native_temperature(self) -> float | None:
        return self._attrs["temperature"]

    @property
    def native_apparent_temperature(self) -> float | None:
        return self._attrs["apparent_temperature"]

    @property
    def native_dew_point(self) -> float | None:
        return self._attrs["dew_point"]

    @property
    def humidity(self) -> float | None:
        return self._attrs["humidity"]

    @property
    def native_pressure(self) -> float | None:
        return self._attrs["pressure"]

    @property
    def native_wind_speed(self) -> float | None:
        return self._attrs["wind_speed"]

    @property
    def native_wind_gust_speed(self) -> float | None:
        return self._attrs["wind_gust_speed"]

    @property
    def wind_bearing(self) -> float | None:
        return self._attrs["wind_bearing"]

    @property
    def uv_index(self) -> float | None:
        return self._attrs["uv_index"]

    @property
    def native_visibility(self) -> float | None:
        return self._attrs["visibility"]

    @property
    def cloud_coverage(self) -> float | None:
        return self._attrs["cloud_coverage"]

    async def async_forecast_daily(self) -> list[Forecast] | None:
        days = ((self.coordinator.weather or {}).get("forecastDaily") or {}).get("days") or []
        return [Forecast(**item) for item in daily_forecast(days)]
