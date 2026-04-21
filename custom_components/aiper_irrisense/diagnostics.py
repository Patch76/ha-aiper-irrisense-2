"""Diagnostics support for the Aiper Irrisense 2 integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import IrrisenseCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "token",
    "encryptKey",
    "identityId",
    "identityPoolId",
    "developerProviderName",
    "AccessKeyId",
    "SecretKey",
    "SessionToken",
    "serialNumber",
    "sn",
    "iotEndpoint",
    "openid_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    slot = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator: IrrisenseCoordinator | None = slot.get("coordinator")
    api = slot.get("api")

    mqtt_connected = bool(api.is_mqtt_connected()) if api else False
    data = coordinator.data if coordinator and coordinator.data else {}

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "mqtt_connected": mqtt_connected,
        "devices": async_redact_data(
            {sn: _summarize_device_slot(slot) for sn, slot in data.items()},
            TO_REDACT,
        ),
    }


def _summarize_device_slot(slot: dict[str, Any]) -> dict[str, Any]:
    """Strip large/volatile fields from a device slot for diagnostics output."""
    out: dict[str, Any] = {}
    for key in ("equipment", "wr_info", "setting", "tasks", "nozzle", "reminder", "stats"):
        val = slot.get(key)
        if val is None:
            continue
        out[key] = val

    zmap = slot.get("map")
    if isinstance(zmap, dict) and isinstance(zmap.get("regions"), list):
        out["map"] = {
            "regions": [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "type": r.get("type"),
                    "point_count": len(r.get("points") or []) if isinstance(r.get("points"), list) else None,
                }
                for r in zmap["regions"]
                if isinstance(r, dict)
            ]
        }

    mqtt = slot.get("mqtt")
    if isinstance(mqtt, dict):
        out["mqtt_keys"] = sorted(mqtt.keys())

    return out
