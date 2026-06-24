# Design: Weather entity for `aiper_irrisense`

Date: 2026-06-24
Status: approved (brainstorming) → ready for implementation plan
Scope owner: contributor (Patch76) to `Merlz/ha-aiper-irrisense-2`

## Problem / motivation

The Aiper IrriSense cloud exposes a weather endpoint the integration does not use.
It was missed because the original API survey was scoped to the `/wr/` namespace only;
a full-surface sweep (213 Retrofit routes) found `/weatherkit/getWeather` plus other
namespaces (`/family/v1/`, `/equipmentSpecialConsume/`).

`/weatherkit/getWeather` is Aiper's proxy of **Apple WeatherKit**. It is read-only,
location-driven, and confirmed working live. It gives the garden a full current-conditions
+ 10-day forecast feed — the highest-value unbuilt addition.

The originally-filed #29 ("weather-reminder switch + depth/cycles numbers") is a dead end
and is explicitly **out of scope** here (see below).

## Live-verified facts (probe 2026-06-24, `weather_probe.py` / `weather_probe2.py`)

- `POST /weatherkit/getWeather` on the same base host (`apieurope.aiper.com`, region-resolved),
  same AES+RSA encrypted transport as `/wr/`. Returns `code:"200"`, `data` is a **JSON string**
  (Apple WeatherKit payload) that must be parsed.
- Body: `{dataSets:"currentWeather,forecastDaily", language:"en", latitude:<float>, longitude:<float>, reverseGeocodingValue:""}`.
- **Honours caller-supplied lat/lng** (sent 53.55/9.99 → metadata echoed 53.55/9.99). It does
  NOT auto-inject a device location; with no coords it defaults to 0,0. → drive it with HA's own
  home coordinates; the Aiper family-location call is not needed.
- `currentWeather` (21 fields): asOf, temperature, temperatureApparent, temperatureDewPoint,
  humidity, pressure, pressureTrend, precipitationIntensity, conditionCode, cloudCover (+High/Low/Mid),
  uvIndex, visibility, windSpeed, windGust, windDirection, daylight.
- `forecastDaily.days` = **10 days**, each: conditionCode, temperatureMax/Min, precipitationChance,
  precipitationAmount, precipitationType, snowfallAmount, maxUvIndex, windSpeedAvg/Max, windGustSpeedMax,
  sunrise/sunset (+Civil/Nautical), moonPhase/rise/set, solarNoon/Midnight, forecastStart/End,
  daytime/overnight/restOfDayForecast.
- Units: WeatherKit returns metric (`metadata.units:"m"`).

Dead-end facts (also live-confirmed, kept for the record — NOT built):
- `getReminderSetting` live returns only 4 flags (no `weatherReminder`); `updateWeatherReminderSetting`
  → 404 (non-enveloped response). The weatherReminder switch is unbuildable.
- `getWateringSettingV2` live returns only rain/wind (no depth/cycles). Depth/cycles are read by the
  app over BLE only; from the cloud they are write-only → no real HA state. Not built.

## Decisions (from brainstorming)

1. **Form**: native HA `weather` entities (not individual sensors). Forecast via `async_forecast_daily`.
2. **Coordinates**: HA home coordinates now (`hass.config.latitude/longitude`), via a single
   per-device resolver (`_resolve_coords(sn)`) so per-device Aiper location can be added later
   without restructuring.
3. **Cardinality**: **one weather entity per device** (revised 2026-06-24 — was "one entity on the
   primary device", which was asymmetric and not the per-device end shape). All devices resolve to
   HA home coords today (same data), so the coordinator **dedups the fetch by coordinate** — one API
   call while coords match, scaling to N only when per-device location diverges. The entity set is
   stable: per-device location later changes only the internal coord source, no entity churn.

## Architecture

Three small, independently-testable units:

### 1. API method — `api.py`
```
def get_weather(self, latitude: float, longitude: float) -> dict | None
```
- Calls `/weatherkit/getWeather` via the existing encrypted call path.
- Parses the JSON-string `data` into a dict. Returns the parsed `{currentWeather, forecastDaily}` or
  `None` on failure (no raise — matches existing read helpers; coordinator decides on stale data).
- No new transport code; reuses `_call_encrypted` / session.

### 2. Coordinator — `coordinator.py`
- New cached slot on its own cadence, mirroring the existing `_last_*_fetch` / `_*_refresh` tiers.
  - `_weather_refresh` (seconds), default **1 h**; `_last_weather_fetch` timestamp.
    Rationale: weather is one location-level call per interval (not per device/zone), and Apple
    WeatherKit only updates ~hourly — sub-hourly polling adds load with no data benefit. API rate
    limits are unknown, so default conservative + user-configurable.
- `_resolve_coords(sn) -> tuple[float, float] | None` — returns
  `(hass.config.latitude, hass.config.longitude)` for every `sn` today; the single per-device seam for
  per-device location later. Returns `None` (→ skip that device's fetch) if HA has no home location.
- Weather stored **per-SN** (`self._weather: dict[sn, payload]`, read via `weather_for(sn)`). The
  refresh **dedups by coordinate**: build a `{coords: [sn,...]}` map, fetch once per unique coordinate,
  assign to all SNs sharing it — one API call while coords match, N when per-device location diverges.
- Fetch is additive and **failure-isolated**: a weather failure / rate-limit (e.g. 429) must be
  swallowed and must not fail the device refresh or touch the MQTT control path. Watering must keep
  working even if weather is rate-limited. Keep last-good payload on failure.

### 3. Weather entity — `weather.py`
- `IrrisenseWeather(IrrisenseEntity, WeatherEntity)`, **one instance per device** (loop
  `coordinator.devices`), each reading its own SN via `coordinator.weather_for(sn)`.
- `_attr_supported_features = WeatherEntityFeature.FORECAST_DAILY`.
- Native properties from `currentWeather`:
  | HA property | source |
  |---|---|
  | `native_temperature` | temperature |
  | `native_apparent_temperature` | temperatureApparent |
  | `native_dew_point` | temperatureDewPoint |
  | `humidity` | humidity × 100 (WeatherKit 0–1 → %) |
  | `native_pressure` | pressure |
  | `native_wind_speed` | windSpeed |
  | `native_wind_gust_speed` | windGust |
  | `wind_bearing` | windDirection |
  | `uv_index` | uvIndex |
  | `native_visibility` | visibility |
  | `cloud_coverage` | cloudCover × 100 |
  | `condition` | conditionCode → HA-condition map (below), `daylight` picks sunny vs clear-night |
- `async_forecast_daily()` builds `Forecast` items from `forecastDaily.days`:
  datetime (forecastStart), condition, `native_temperature` (temperatureMax),
  `native_templow` (temperatureMin), `precipitation_probability` (precipitationChance × 100),
  `native_precipitation` (precipitationAmount), `uv_index` (maxUvIndex), `native_wind_speed` (windSpeedMax).
- Native unit attributes set to metric (WeatherKit is metric); HA converts to the user's display units.

### Condition mapping (`conditionCode` → HA condition)
Module-level dict. Known/seen + standard Apple WeatherKit set; unknown codes fall back to a best-effort
default and log once. `daylight` flag resolves Clear/MostlyClear to `sunny` (day) or `clear-night`.

| Apple conditionCode | HA condition |
|---|---|
| Clear, MostlyClear | sunny / clear-night |
| PartlyCloudy | partlycloudy |
| MostlyCloudy, Cloudy | cloudy |
| Foggy, Haze, Smoky | fog |
| Breezy, Windy | windy |
| Drizzle, Rain, Showers, Precipitation | rainy |
| HeavyRain | pouring |
| Thunderstorms, IsolatedThunderstorms, ScatteredThunderstorms, StrongStorms | lightning-rainy |
| Flurries, Snow, HeavySnow, SnowShowers, BlowingSnow, Blizzard | snowy |
| Sleet, FreezingRain, FreezingDrizzle, Wintry Mix | snowy-rainy |
| Hail | hail |
| Hot, Frigid, Hurricane, TropicalStorm | exceptional |
| (unknown) | fallback: cloudy + log once |

⚠️ The exact full Apple conditionCode enum (~40 values) and the exact current HA `WeatherEntity` API
(`native_*` attribute names, `Forecast` TypedDict keys, `WeatherEntityFeature`) must be verified against
the installed Home Assistant version during implementation, not assumed from this table.

## Config / options
- Add `Platform.WEATHER` to `PLATFORMS` in `__init__.py`.
- Add `CONF_WEATHER_REFRESH_HOURS` + `DEFAULT_WEATHER_REFRESH_HOURS = 1` in `const.py`, wired into
  the coordinator options like the other `*_refresh_hours`.
- `strings.json` / `translations/en.json`: option label + entity name.

## Out of scope (separate, gated GitHub follow-ups — not this build)
- Re-frame issue #29 away from the dead weather-reminder toggle toward these weather sensors.
- Note on #30 that the pesticide read path is `/equipmentSpecialConsume/getPesticideSpecialConsumeList`
  (live 200 but empty on the test devices → model unconfirmed; `getPesticideSpecialConsumeStatus`
  returns 6002 with `{sn}` → body shape unknown).
- Per-device weather location (resolve the `getFamilyManagement` body that returned 5015).
These require explicit verb+object approval before any GitHub post.

## Testing
Merlz has no CI → local unit tests only.
- `conditionCode` map: known codes map correctly; unknown → fallback.
- Forecast parsing: feed the real probe JSON (capture as a fixture) → assert the `Forecast` list shape,
  unit fields, day count.
- `humidity`/`cloud_coverage` 0–1 → percent scaling.
- Coordinate resolver: returns home coords; returns `None` when HA home location is unset (→ fetch skipped).
- Weather-fetch failure does not break the per-device refresh.

## Open questions / residual uncertainty
- ⚠️ Full Apple `conditionCode` enum + current HA `WeatherEntity` API surface — verify live in implementation.
- ⚠️ Per-device location (`getFamilyManagement` body) — deferred by decision.
- ⚠️ Pesticide consume model — unverifiable on the test hardware (no cartridge) — out of scope.
