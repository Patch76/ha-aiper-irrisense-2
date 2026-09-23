"""Select platform for the Aiper Irrisense 2.

Three selects per device:

* **Nozzle type** — Standard / Jet, persisted to server.
* **Watering zone** — list of zones from the cached zone map.
  Current value is stored on the coordinator, not on the entity, so it
  survives the map-refresh entity rebuild and stays in sync with the
  Start / Stop buttons.
* **Dose** — dynamic options + name driven by the currently-selected
  zone's type:
    * Area / Line → "Dose",     options ``3 mm / 6 mm / 13 mm``
    * Point       → "Duration", options ``1 min / 5 min / 10 min``
  plus the current value when it was set freely through a Number entity.
  Options are derived from the coordinator on every read; the name and
  icon swap via :data:`SIGNAL_SELECTION_CHANGED`, which the coordinator
  fires on every zone or dose change.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    NOZZLE_SERVER_TO_DEVICE,
    NOZZLE_TYPE_LABELS,
    REGION_TYPE_POINT,
    dose_options_for_region_type,
)
from .coordinator import (
    SIGNAL_MAP_UPDATED,
    SIGNAL_SELECTION_CHANGED,
    IrrisenseCoordinator,
)
from .entity import IrrisenseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrisenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[SelectEntity] = []
    for dev in coordinator.devices:
        sn = dev.get("sn")
        if not sn:
            continue
        entities.append(NozzleTypeSelect(coordinator, sn))
        entities.append(ZoneSelect(coordinator, sn))
        entities.append(DoseSelect(coordinator, sn))
    async_add_entities(entities)


# --------------------------------------------------------------------------- #
# Nozzle type
# --------------------------------------------------------------------------- #


class NozzleTypeSelect(IrrisenseEntity, SelectEntity):
    _attr_icon = "mdi:water-pump"
    _attr_translation_key = "nozzle_type"

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "nozzle_type")
        self._attr_options = list(NOZZLE_TYPE_LABELS.values())

    @property
    def current_option(self) -> str | None:
        nz = self._slot.get("nozzle")
        if not isinstance(nz, dict):
            return None
        for k in ("nozzleType", "nozzle_type", "type"):
            raw = nz.get(k)
            if isinstance(raw, str) and raw.lstrip("-").isdigit():
                raw = int(raw)
            if not isinstance(raw, int):
                continue
            # `nz` comes from /wr/getNozzleTypeSetting, which uses the
            # 1-indexed server encoding: {0,1}→Standard, 2→Jet.
            device_idx = NOZZLE_SERVER_TO_DEVICE.get(raw)
            if device_idx is None:
                continue
            return NOZZLE_TYPE_LABELS.get(device_idx)
        return None

    async def async_select_option(self, option: str) -> None:
        lookup = {label: code for code, label in NOZZLE_TYPE_LABELS.items()}
        if option not in lookup:
            return
        await self.coordinator.async_set_nozzle_type(self._sn, lookup[option])


# --------------------------------------------------------------------------- #
# Zone + Dose selects
# --------------------------------------------------------------------------- #


def _label_for_region(r: dict[str, Any]) -> str:
    """Build a stable display label for a zone.

    We append the type tag (Area/Line/Point) so the user sees at-a-glance
    which dose vocabulary is about to appear, and so duplicate names like
    two "Point 1" zones remain distinguishable.
    """
    name = str(r.get("name") or f"Zone {r.get('id')}")
    rtype = int(r.get("type", 0))
    tag = {0: "Area", 1: "Line", 2: "Point"}.get(rtype, "Zone")
    return f"{name} ({tag})"


class ZoneSelect(IrrisenseEntity, SelectEntity, RestoreEntity):
    """Lets the user pick which zone to start next.

    Current value is stored on the coordinator (not on the entity) so the
    Start button can read it without going through the HA state machine,
    and so that a map refresh — which may tear down and recreate the
    entity — doesn't lose the selection.
    """

    _attr_icon = "mdi:map-marker-radius"
    _attr_translation_key = "watering_zone"

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "watering_zone")
        self._refresh_options()

    # ----- Options ---------------------------------------------------------

    def _refresh_options(self) -> None:
        regions = self.coordinator.zones_for(self._sn)
        self._attr_options = [_label_for_region(r) for r in regions]

    def _zone_id_for_label(self, label: str) -> int | None:
        for r in self.coordinator.zones_for(self._sn):
            if _label_for_region(r) == label:
                zid = r.get("id")
                return int(zid) if isinstance(zid, int) else None
        return None

    def _label_for_zone_id(self, zone_id: int) -> str | None:
        for r in self.coordinator.zones_for(self._sn):
            if r.get("id") == zone_id:
                return _label_for_region(r)
        return None

    # ----- State ------------------------------------------------------------

    @property
    def current_option(self) -> str | None:
        zid = self.coordinator.get_zone_selection(self._sn)
        if zid is None:
            return None
        return self._label_for_zone_id(zid)

    async def async_select_option(self, option: str) -> None:
        zid = self._zone_id_for_label(option)
        if zid is None:
            _LOGGER.warning("ZoneSelect: unknown zone label %r", option)
            return
        self.coordinator.set_zone_selection(self._sn, zid)
        # coordinator fires SIGNAL_SELECTION_CHANGED; dose select re-renders.
        self.async_write_ha_state()

    # ----- Lifecycle --------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore the last picked zone label across HA restarts. We match by
        # label (the user-facing display form); if the zone has been
        # renamed or removed, coordinator.get_zone_selection falls back to
        # the first available zone automatically.
        last = await self.async_get_last_state()
        if last and last.state and last.state not in ("unknown", "unavailable"):
            zid = self._zone_id_for_label(last.state)
            if zid is not None:
                self.coordinator.set_zone_selection(self._sn, zid)

        # Re-render options on map refresh (new zones, renamed zones, etc.)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_MAP_UPDATED, self._on_map_updated
            )
        )

    @callback
    def _on_map_updated(self, sn: str, regions: list[dict[str, Any]]) -> None:
        if sn != self._sn:
            return
        self._refresh_options()
        self.async_write_ha_state()


class DoseSelect(IrrisenseEntity, SelectEntity, RestoreEntity):
    """Dose (mm) for Area/Line zones or Duration (min) for Point zones.

    Both the list of options and the entity name adapt to the currently-
    selected zone's type. Backend translation to wire values is done in
    the Start button.
    """

    def __init__(self, coordinator: IrrisenseCoordinator, sn: str) -> None:
        super().__init__(coordinator, sn, "watering_dose")
        self._apply_region_type(coordinator.selected_region_type(sn))

    # ----- Dynamic shape ---------------------------------------------------

    def _apply_region_type(self, region_type: int) -> None:
        """Rewrite name + icon to match the zone type."""
        if region_type == REGION_TYPE_POINT:
            self._attr_icon = "mdi:timer-outline"
            self._attr_translation_key = "watering_duration"
        else:
            self._attr_icon = "mdi:water"
            self._attr_translation_key = "watering_dose"

    # ----- State ------------------------------------------------------------

    @property
    def options(self) -> list[str]:
        """Presets for the selected zone type, plus a fitting free value.

        Derived on every read so a value set through a Number entity shows
        here as it is instead of as the type default.
        """
        return dose_options_for_region_type(
            self.coordinator.selected_region_type(self._sn),
            self.coordinator.get_dose_selection(self._sn),
        )

    @property
    def current_option(self) -> str | None:
        # Always one of `options`: the coordinator returns either a fitting
        # pick (preset, or the free value `options` adds) or the type default.
        return self.coordinator.get_dose_selection(self._sn)

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            _LOGGER.warning(
                "DoseSelect: %r is not one of the current options %s",
                option, self.options,
            )
            return
        self.coordinator.set_dose_selection(self._sn, option)
        self.async_write_ha_state()

    # ----- Lifecycle --------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore the last dose label. The coordinator refuses one that does
        # not fit the (already restored) zone's type; the type default then
        # shows instead.
        last = await self.async_get_last_state()
        if last and last.state and last.state not in ("unknown", "unavailable"):
            if not self.coordinator.set_dose_selection(self._sn, last.state):
                _LOGGER.debug(
                    "DoseSelect: restored dose %r does not fit the selected zone "
                    "(sn=%s); showing the type default",
                    last.state, self._sn,
                )

        # Listen for zone-selection changes so we can re-render.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_SELECTION_CHANGED, self._on_selection_changed
            )
        )
        # Also re-render on map updates — if the selected zone's type
        # changed (or the zone vanished and the default moved), the
        # option list needs to follow.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_MAP_UPDATED, self._on_map_updated
            )
        )

    @callback
    def _on_selection_changed(self, sn: str) -> None:
        if sn != self._sn:
            return
        self._apply_region_type(self.coordinator.selected_region_type(sn))
        self.async_write_ha_state()

    @callback
    def _on_map_updated(self, sn: str, regions: list[dict[str, Any]]) -> None:
        if sn != self._sn:
            return
        self._apply_region_type(self.coordinator.selected_region_type(sn))
        self.async_write_ha_state()
