# Aiper Weather Entity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native Home Assistant `weather` entity to `aiper_irrisense`, fed by the live-confirmed Apple-WeatherKit-proxied `/weatherkit/getWeather` cloud endpoint, driven by HA's own home coordinates.

**Architecture:** A pure-logic layer (`weather_helpers.py`: condition mapping, forecast/current parsing, coordinate resolution, JSON-payload parsing) with stdlib-pytest unit tests, plus an HA-glue layer (api read method, coordinator slot, `WeatherEntity`) validated live on the LB-HA testbed. Weather is one location-level fetch per interval, failure-isolated so a rate-limit can never break watering.

**Tech Stack:** Python 3.11+, Home Assistant custom component, `requests` (existing encrypted client), stdlib `json`, pytest (pure-logic tests only).

## Global Constraints

- Repo `Merlz/ha-aiper-irrisense-2` — **no CI, no existing test harness**. Pure-logic tests use stdlib pytest only (no `homeassistant` import). HA-coupled code is validated live on LB-HA (HACS update of `Patch76` fork main → restart → observe).
- Read-only feature. **Watering must keep working even if weather fails/rate-limits** — every weather fetch is wrapped and swallowed; never touches the MQTT control path or fails the device refresh.
- Weather refresh default **1 hour**, user-configurable (`CONF_WEATHER_REFRESH_HOURS`).
- One weather entity, tied to the **primary device SN** (`coordinator.devices[0]["sn"]`), `unique_id = f"{sn}_weather"`. Per-device location is deferred; coordinate source goes through ONE resolver function as the seam.
- Units: WeatherKit returns metric — temperature °C, pressure millibars (hPa), wind km/h, visibility meters, precipitation mm. Set native units accordingly; HA converts to display units.
- WeatherKit fractions are 0–1: `humidity`, `cloudCover`, `precipitationChance` → ×100 for HA percent fields.
- ⚠️ **Verify in implementation, do not assume:** the current HA `WeatherEntity` API (`native_*` attribute names, `Forecast` TypedDict keys, `WeatherEntityFeature.FORECAST_DAILY`, `async_forecast_daily`) against the installed HA version; and the full Apple `conditionCode` enum (the map below covers the common set + a logged fallback).
- Commit style: conventional commits, end body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. **Do not push / do not open a PR** without explicit verb+object approval.

---

## File Structure

- Create `custom_components/aiper_irrisense/weather_helpers.py` — pure functions: condition map, current-attrs, daily-forecast builder, coord resolver, payload parser. No HA imports.
- Create `custom_components/aiper_irrisense/weather.py` — `IrrisenseWeather(IrrisenseEntity, WeatherEntity)`, thin wrapper over helpers.
- Modify `custom_components/aiper_irrisense/api.py` — add `get_weather(latitude, longitude)`.
- Modify `custom_components/aiper_irrisense/const.py` — add `CONF_WEATHER_REFRESH_HOURS`, `DEFAULT_WEATHER_REFRESH_HOURS`.
- Modify `custom_components/aiper_irrisense/coordinator.py` — weather slot: attribute, resolver, refresh, property, loop wiring.
- Modify `custom_components/aiper_irrisense/__init__.py` — add `Platform.WEATHER` to `PLATFORMS`.
- Modify `custom_components/aiper_irrisense/config_flow.py` — add weather-refresh option to the OptionsFlow schema.
- Modify `custom_components/aiper_irrisense/strings.json` + `translations/en.json` — option label.
- Create `tests/fixtures/weatherkit_sample.json` — captured real probe payload (parsed object).
- Create `tests/test_weather_helpers.py` — pure-logic tests.

---

## Task 1: API read method `get_weather`

**Files:**
- Modify: `custom_components/aiper_irrisense/api.py` (add method near the other `/wr/` reads, after `get_watering_setting`)
- Create: `custom_components/aiper_irrisense/weather_helpers.py`
- Create: `tests/fixtures/weatherkit_sample.json`
- Create: `tests/test_weather_helpers.py`

**Interfaces:**
- Produces: `weather_helpers.parse_weather_payload(raw: str | dict | None) -> dict | None`
- Produces: `IrrisenseApi.get_weather(self, latitude: float, longitude: float) -> dict | None` — returns `{"currentWeather": {...}, "forecastDaily": {...}}` or `None`.

- [ ] **Step 1: Create the fixture** `tests/fixtures/weatherkit_sample.json` (real probe shape, trimmed to 2 days):

```json
{
  "currentWeather": {
    "asOf": "2026-06-24T18:24:33Z",
    "conditionCode": "PartlyCloudy",
    "daylight": false,
    "humidity": 0.85,
    "cloudCover": 0.53,
    "precipitationIntensity": 0.0,
    "pressure": 1014.78,
    "pressureTrend": "rising",
    "temperature": 23.29,
    "temperatureApparent": 22.58,
    "temperatureDewPoint": 20.62,
    "uvIndex": 0,
    "visibility": 17181.62,
    "windDirection": 133,
    "windGust": 18.82,
    "windSpeed": 14.3
  },
  "forecastDaily": {
    "days": [
      {
        "forecastStart": "2026-06-23T22:00:00Z",
        "forecastEnd": "2026-06-24T22:00:00Z",
        "conditionCode": "MostlyClear",
        "maxUvIndex": 7,
        "precipitationAmount": 0.0,
        "precipitationChance": 0.43,
        "precipitationType": "clear",
        "snowfallAmount": 0.0,
        "temperatureMax": 24.1,
        "temperatureMin": 13.2,
        "windSpeedAvg": 9.0,
        "windSpeedMax": 18.0,
        "windGustSpeedMax": 30.0
      },
      {
        "forecastStart": "2026-06-24T22:00:00Z",
        "forecastEnd": "2026-06-25T22:00:00Z",
        "conditionCode": "Rain",
        "maxUvIndex": 5,
        "precipitationAmount": 3.4,
        "precipitationChance": 0.8,
        "precipitationType": "rain",
        "snowfallAmount": 0.0,
        "temperatureMax": 19.0,
        "temperatureMin": 12.0,
        "windSpeedAvg": 12.0,
        "windSpeedMax": 22.0,
        "windGustSpeedMax": 40.0
      }
    ]
  }
}
```

- [ ] **Step 2: Write the failing test** in `tests/test_weather_helpers.py`:

```python
import json
import pathlib

from custom_components.aiper_irrisense import weather_helpers as wh

FIX = pathlib.Path(__file__).parent / "fixtures" / "weatherkit_sample.json"
SAMPLE = FIX.read_text()


def test_parse_weather_payload_from_json_string():
    # WeatherKit returns the payload as a JSON *string* in data
    out = wh.parse_weather_payload(SAMPLE)
    assert out is not None
    assert out["currentWeather"]["temperature"] == 23.29
    assert len(out["forecastDaily"]["days"]) == 2


def test_parse_weather_payload_passthrough_dict():
    d = json.loads(SAMPLE)
    assert wh.parse_weather_payload(d) == d


def test_parse_weather_payload_garbage_returns_none():
    assert wh.parse_weather_payload("not json") is None
    assert wh.parse_weather_payload(None) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_weather_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError: module ... has no attribute 'parse_weather_payload'`

- [ ] **Step 4: Implement `parse_weather_payload`** in `weather_helpers.py`:

```python
"""Pure (HA-free) helpers for the Aiper weather entity. Unit-tested with stdlib pytest."""
from __future__ import annotations

import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


def parse_weather_payload(raw: str | dict | None) -> dict | None:
    """`/weatherkit/getWeather` returns its payload as a JSON *string* in `data`.

    Accept the string (parse it), a pre-parsed dict (passthrough), or None.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            _LOGGER.debug("weather: data not JSON: %.120s", raw)
            return None
        return obj if isinstance(obj, dict) else None
    return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_weather_helpers.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Add `get_weather` to `api.py`** (after `get_watering_setting`, ~line 559):

```python
    def get_weather(self, latitude: float, longitude: float) -> dict | None:
        """Apple-WeatherKit-proxied forecast. Read-only. Returns parsed
        {currentWeather, forecastDaily} or None. data arrives as a JSON string."""
        from .weather_helpers import parse_weather_payload

        raw = self._wr(
            "/weatherkit/getWeather",
            {
                "dataSets": "currentWeather,forecastDaily",
                "language": "en",
                "latitude": float(latitude),
                "longitude": float(longitude),
                "reverseGeocodingValue": "",
            },
        )
        return parse_weather_payload(raw)
```

- [ ] **Step 7: Commit**

```bash
git add custom_components/aiper_irrisense/weather_helpers.py custom_components/aiper_irrisense/api.py tests/
git commit -m "feat(weather): add get_weather API read + payload parser

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Pure mapping helpers (condition, current attrs, daily forecast, coords)

**Files:**
- Modify: `custom_components/aiper_irrisense/weather_helpers.py`
- Modify: `tests/test_weather_helpers.py`

**Interfaces:**
- Consumes: `parse_weather_payload` (Task 1).
- Produces:
  - `ha_condition(condition_code: str | None, daylight: bool) -> str`
  - `current_attrs(current: dict) -> dict` — keys: `temperature, apparent_temperature, dew_point, humidity, pressure, wind_speed, wind_gust_speed, wind_bearing, uv_index, visibility, cloud_coverage, condition`
  - `daily_forecast(days: list[dict]) -> list[dict]` — each: `datetime, condition, native_temperature, native_templow, precipitation_probability, native_precipitation, uv_index, native_wind_speed`
  - `resolve_coords(latitude: float | None, longitude: float | None) -> tuple[float, float] | None`

- [ ] **Step 1: Write failing tests** (append to `tests/test_weather_helpers.py`):

```python
def test_ha_condition_known_codes():
    assert wh.ha_condition("Clear", daylight=True) == "sunny"
    assert wh.ha_condition("Clear", daylight=False) == "clear-night"
    assert wh.ha_condition("PartlyCloudy", daylight=True) == "partlycloudy"
    assert wh.ha_condition("Rain", daylight=True) == "rainy"
    assert wh.ha_condition("HeavyRain", daylight=True) == "pouring"
    assert wh.ha_condition("Thunderstorms", daylight=True) == "lightning-rainy"
    assert wh.ha_condition("Snow", daylight=True) == "snowy"


def test_ha_condition_unknown_falls_back():
    assert wh.ha_condition("SomeNewAppleCode", daylight=True) == "cloudy"
    assert wh.ha_condition(None, daylight=True) == "cloudy"


def test_current_attrs_scales_fractions():
    cur = json.loads(SAMPLE)["currentWeather"]
    a = wh.current_attrs(cur)
    assert a["temperature"] == 23.29
    assert a["humidity"] == 85.0          # 0.85 -> %
    assert a["cloud_coverage"] == 53.0    # 0.53 -> %
    assert a["wind_bearing"] == 133
    assert a["condition"] == "partlycloudy"  # PartlyCloudy, daylight false


def test_daily_forecast_shape():
    days = json.loads(SAMPLE)["forecastDaily"]["days"]
    fc = wh.daily_forecast(days)
    assert len(fc) == 2
    d0 = fc[0]
    assert d0["datetime"] == "2026-06-23T22:00:00Z"
    assert d0["native_temperature"] == 24.1
    assert d0["native_templow"] == 13.2
    assert d0["precipitation_probability"] == 43.0   # 0.43 -> %
    assert d0["native_precipitation"] == 0.0
    assert fc[1]["condition"] == "rainy"
    assert fc[1]["native_precipitation"] == 3.4


def test_resolve_coords():
    assert wh.resolve_coords(53.55, 9.99) == (53.55, 9.99)
    assert wh.resolve_coords(None, 9.99) is None
    assert wh.resolve_coords(0.0, 0.0) is None   # unset HA home -> skip
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_weather_helpers.py -v`
Expected: FAIL — `AttributeError: ... 'ha_condition'`

- [ ] **Step 3: Implement the helpers** (append to `weather_helpers.py`):

```python
# Apple WeatherKit conditionCode -> HA condition. daylight picks sunny/clear-night.
# Covers the common set; unknown codes log once and fall back to "cloudy".
_CONDITION_MAP: dict[str, str] = {
    "Clear": "sunny",
    "MostlyClear": "sunny",
    "PartlyCloudy": "partlycloudy",
    "MostlyCloudy": "cloudy",
    "Cloudy": "cloudy",
    "Foggy": "fog",
    "Haze": "fog",
    "Smoky": "fog",
    "Breezy": "windy",
    "Windy": "windy",
    "Drizzle": "rainy",
    "Rain": "rainy",
    "Showers": "rainy",
    "Precipitation": "rainy",
    "HeavyRain": "pouring",
    "Thunderstorms": "lightning-rainy",
    "IsolatedThunderstorms": "lightning-rainy",
    "ScatteredThunderstorms": "lightning-rainy",
    "StrongStorms": "lightning-rainy",
    "Flurries": "snowy",
    "Snow": "snowy",
    "HeavySnow": "snowy",
    "SnowShowers": "snowy",
    "BlowingSnow": "snowy",
    "Blizzard": "snowy",
    "Sleet": "snowy-rainy",
    "FreezingRain": "snowy-rainy",
    "FreezingDrizzle": "snowy-rainy",
    "WintryMix": "snowy-rainy",
    "Hail": "hail",
    "Hot": "exceptional",
    "Frigid": "exceptional",
    "Hurricane": "exceptional",
    "TropicalStorm": "exceptional",
}

_seen_unknown: set[str] = set()


def ha_condition(condition_code: str | None, daylight: bool) -> str:
    if condition_code in ("Clear", "MostlyClear"):
        return "sunny" if daylight else "clear-night"
    mapped = _CONDITION_MAP.get(condition_code or "")
    if mapped is None:
        if condition_code and condition_code not in _seen_unknown:
            _seen_unknown.add(condition_code)
            _LOGGER.warning("Unmapped WeatherKit conditionCode %r -> cloudy", condition_code)
        return "cloudy"
    return mapped


def _pct(value: Any) -> float | None:
    """WeatherKit 0-1 fraction -> HA percent."""
    if isinstance(value, (int, float)):
        return round(float(value) * 100, 1)
    return None


def current_attrs(current: dict) -> dict:
    c = current or {}
    return {
        "temperature": c.get("temperature"),
        "apparent_temperature": c.get("temperatureApparent"),
        "dew_point": c.get("temperatureDewPoint"),
        "humidity": _pct(c.get("humidity")),
        "pressure": c.get("pressure"),
        "wind_speed": c.get("windSpeed"),
        "wind_gust_speed": c.get("windGust"),
        "wind_bearing": c.get("windDirection"),
        "uv_index": c.get("uvIndex"),
        "visibility": c.get("visibility"),
        "cloud_coverage": _pct(c.get("cloudCover")),
        "condition": ha_condition(c.get("conditionCode"), bool(c.get("daylight"))),
    }


def daily_forecast(days: list[dict]) -> list[dict]:
    out: list[dict] = []
    for d in days or []:
        if not isinstance(d, dict):
            continue
        out.append(
            {
                "datetime": d.get("forecastStart"),
                "condition": ha_condition(d.get("conditionCode"), daylight=True),
                "native_temperature": d.get("temperatureMax"),
                "native_templow": d.get("temperatureMin"),
                "precipitation_probability": _pct(d.get("precipitationChance")),
                "native_precipitation": d.get("precipitationAmount"),
                "uv_index": d.get("maxUvIndex"),
                "native_wind_speed": d.get("windSpeedMax"),
            }
        )
    return out


def resolve_coords(
    latitude: float | None, longitude: float | None
) -> tuple[float, float] | None:
    if latitude is None or longitude is None:
        return None
    if latitude == 0 and longitude == 0:
        return None
    return (float(latitude), float(longitude))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_weather_helpers.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add custom_components/aiper_irrisense/weather_helpers.py tests/test_weather_helpers.py
git commit -m "feat(weather): pure condition map + current/forecast/coords helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Coordinator weather slot

**Files:**
- Modify: `custom_components/aiper_irrisense/const.py` (after the `*_REFRESH_HOURS` block, ~line 36 + 43)
- Modify: `custom_components/aiper_irrisense/coordinator.py` (`__init__` ~line 159-204, update loop ~line 240-254)

**Interfaces:**
- Consumes: `IrrisenseApi.get_weather` (Task 1), `weather_helpers.resolve_coords` (Task 2).
- Produces: `IrrisenseCoordinator.weather` property → `dict | None` (`{currentWeather, forecastDaily}`).

- [ ] **Step 1: Add constants** to `const.py`:

```python
CONF_WEATHER_REFRESH_HOURS: Final = "weather_refresh_hours"
```
(next to the other `CONF_*_REFRESH_HOURS`, line ~36), and:
```python
DEFAULT_WEATHER_REFRESH_HOURS: Final = 1
```
(next to the other `DEFAULT_*_REFRESH_HOURS`, line ~43).

- [ ] **Step 2: Import + init state** in `coordinator.py`. Add to the imports from `.const`:
`CONF_WEATHER_REFRESH_HOURS, DEFAULT_WEATHER_REFRESH_HOURS`. In `__init__`, next to the other `_last_*_fetch` (line ~162) add:

```python
        self._last_weather_fetch: float = 0.0
        self._weather: dict[str, Any] | None = None
```
and next to the other cadence lines (line ~203) add:
```python
        self._weather_refresh = int(opts.get(CONF_WEATHER_REFRESH_HOURS, DEFAULT_WEATHER_REFRESH_HOURS)) * 3600
```

- [ ] **Step 3: Add the property + refresh method** (after the `devices` property, ~line 215):

```python
    @property
    def weather(self) -> dict[str, Any] | None:
        """Latest parsed WeatherKit payload (location-level), or None."""
        return self._weather

    async def _refresh_weather(self) -> None:
        """Fetch weather at most every `_weather_refresh`. Failure-isolated:
        any error is swallowed and last-good is kept so watering is never
        affected by a weather rate-limit."""
        from .weather_helpers import resolve_coords

        now = time.time()
        if self._weather is not None and now - self._last_weather_fetch < self._weather_refresh:
            return
        coords = resolve_coords(self.hass.config.latitude, self.hass.config.longitude)
        if coords is None:
            return
        try:
            w = await self.hass.async_add_executor_job(self.api.get_weather, coords[0], coords[1])
        except Exception as err:  # noqa: BLE001 - weather must never break the refresh
            _LOGGER.debug("weather refresh failed (ignored): %s", err)
            return
        if isinstance(w, dict):
            self._weather = w
            self._last_weather_fetch = now
```

- [ ] **Step 4: Wire into the update loop.** In `_async_update_data`, after the per-device loop that calls `_refresh_device` (after line ~250, still inside the method) add:

```python
        await self._refresh_weather()
```

- [ ] **Step 5: Syntax/import check**

Run: `python -c "import ast; ast.parse(open('custom_components/aiper_irrisense/coordinator.py').read()); ast.parse(open('custom_components/aiper_irrisense/const.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add custom_components/aiper_irrisense/const.py custom_components/aiper_irrisense/coordinator.py
git commit -m "feat(weather): coordinator weather slot (1h, failure-isolated)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Weather entity + platform/config wiring

**Files:**
- Create: `custom_components/aiper_irrisense/weather.py`
- Modify: `custom_components/aiper_irrisense/__init__.py` (`PLATFORMS`, line ~25-30)
- Modify: `custom_components/aiper_irrisense/config_flow.py` (OptionsFlow schema, ~line 168-178; imports ~line 18-26)
- Modify: `custom_components/aiper_irrisense/strings.json` + `translations/en.json` (options labels)

**Interfaces:**
- Consumes: `IrrisenseCoordinator.weather` (Task 3); `weather_helpers.current_attrs`, `daily_forecast` (Task 2); `IrrisenseEntity.__init__(coordinator, sn, key)`.

- [ ] **Step 1: Create `weather.py`**

```python
"""Weather platform for Aiper Irrisense 2 — Apple-WeatherKit-proxied cloud data.

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
```

- [ ] **Step 2: Register the platform** in `__init__.py` — add to `PLATFORMS`:

```python
    Platform.WEATHER,
```

- [ ] **Step 3: Add the config option** in `config_flow.py`. Add to the `.const` imports:
`CONF_WEATHER_REFRESH_HOURS, DEFAULT_WEATHER_REFRESH_HOURS`. In `OptionsFlowHandler.async_step_init`'s schema, after the `CONF_REMINDER_REFRESH_HOURS` entry:

```python
                vol.Optional(
                    CONF_WEATHER_REFRESH_HOURS,
                    default=current.get(CONF_WEATHER_REFRESH_HOURS, DEFAULT_WEATHER_REFRESH_HOURS),
                ): vol.Coerce(float),
```
(Match the coercion type used by the neighbouring refresh-hours fields — read the existing lines and mirror them exactly; if they use `vol.Coerce(int)`, use `int`.)

- [ ] **Step 4: Add the option label** to `strings.json` and `translations/en.json` under the options-step `data` block, mirroring the existing `*_refresh_hours` labels:

```json
"weather_refresh_hours": "Weather refresh (hours)"
```

- [ ] **Step 5: Syntax check**

Run: `python -c "import ast; [ast.parse(open(f).read()) for f in ['custom_components/aiper_irrisense/weather.py','custom_components/aiper_irrisense/__init__.py','custom_components/aiper_irrisense/config_flow.py']]; import json; json.load(open('custom_components/aiper_irrisense/strings.json')); json.load(open('custom_components/aiper_irrisense/translations/en.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Full pure-logic test run**

Run: `python -m pytest tests/ -v`
Expected: PASS (all helper tests)

- [ ] **Step 7: Commit**

```bash
git add custom_components/aiper_irrisense/weather.py custom_components/aiper_irrisense/__init__.py custom_components/aiper_irrisense/config_flow.py custom_components/aiper_irrisense/strings.json custom_components/aiper_irrisense/translations/en.json
git commit -m "feat(weather): WeatherEntity + platform/config wiring

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Live validation on LB-HA (no CI substitute)

**Files:** none (validation only).

This is how the project validates HA-coupled code (no test harness exists). Requires user action (HACS update + restart). Each sub-step is a user-run check; record the result.

- [ ] **Step 1:** Push the branch to the `Patch76` fork and have the user HACS-update + restart HA (gated — needs verb+object approval to push).
- [ ] **Step 2:** Confirm the `weather.irrisense_*` entity exists and is not `unavailable` after one refresh cycle.
- [ ] **Step 3:** Confirm `state` (condition) is sane and current attributes populate (temperature, humidity %, wind, uv).
- [ ] **Step 4:** Confirm the forecast renders: add a Weather Forecast card (daily) and verify ~10 days with temp high/low + precip probability.
- [ ] **Step 5:** Confirm watering still works (trigger a manual zone start or verify the control entities are responsive) — proves weather did not disturb the control path.
- [ ] **Step 6:** Check the HA log for `Unmapped WeatherKit conditionCode` warnings; if any appear, add those codes to `_CONDITION_MAP` (one follow-up commit).

---

## Self-Review

**Spec coverage:**
- Weather entity (native, forecast_daily) → Task 4. ✓
- HA home coords + swappable resolver → Task 2 (`resolve_coords`) + Task 3 (`_refresh_weather`). ✓
- One entity, primary SN → Task 4 setup. ✓
- API method + JSON-string parse → Task 1. ✓
- Condition map → Task 2. ✓
- 1h refresh, configurable, failure-isolated → Task 3 + Task 4 (config). ✓
- Units metric → Task 4 native unit attrs. ✓
- Out-of-scope (#29/#30 re-frame, per-device, pesticide) → intentionally excluded; gated follow-ups. ✓
- Testing: pure-logic pytest (Tasks 1-2) + live validation (Task 5). ✓

**Placeholder scan:** Step 3 of Task 4 says "mirror the existing coercion type" — this is a deliberate read-then-match instruction with a concrete fallback, not an unfilled placeholder. All code steps contain complete code. No TBD/TODO.

**Type consistency:** `parse_weather_payload`, `ha_condition`, `current_attrs`, `daily_forecast`, `resolve_coords` signatures match between definition (Tasks 1-2) and use (Tasks 3-4). `current_attrs` dict keys match the property accessors in `weather.py`. `daily_forecast` keys are valid HA `Forecast` keys (verify against installed HA per Global Constraints). `coordinator.weather` property name consistent across Tasks 3-4.

⚠️ **Residual (carried from spec, verify during implementation):** exact HA `WeatherEntity` API surface + full Apple `conditionCode` enum.
