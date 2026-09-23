"""Unit tests for the per-entry lookup of disabled devices.

`coordinator.py` imports Home Assistant, which the test environment does not
install. Its few HA imports are stubbed and `api.py` (network, crypto) is
replaced, so `disabled_serials` runs against a fake device registry.
"""
import importlib.util
import pathlib
import sys
import types
from dataclasses import dataclass, field

_COMPONENT = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "aiper_irrisense"
)
_PKG = "aiper_registry_under_test"


@dataclass
class _Device:
    identifiers: set
    config_entries: set
    disabled_by: str | None = None


@dataclass
class _Registry:
    devices: list = field(default_factory=list)


REGISTRY = _Registry()


def _entries_for_config_entry(registry, entry_id):
    return [d for d in registry.devices if entry_id in d.config_entries]


def _stub(name, **attrs):
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _Coordinator:
    def __class_getitem__(cls, item):
        return cls


def _load_coordinator():
    if f"{_PKG}.coordinator" in sys.modules:
        return sys.modules[f"{_PKG}.coordinator"]
    for name in ("homeassistant", "homeassistant.helpers"):
        _stub(name)
    _stub("homeassistant.config_entries", ConfigEntry=object)
    _stub("homeassistant.core", HomeAssistant=object)
    _stub(
        "homeassistant.helpers.device_registry",
        async_get=lambda hass: REGISTRY,
        async_entries_for_config_entry=_entries_for_config_entry,
    )
    _stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=None)
    _stub(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_Coordinator,
        UpdateFailed=Exception,
    )
    _stub("homeassistant.helpers.dispatcher", async_dispatcher_send=None)
    _stub(f"{_PKG}.api", IrrisenseApi=object)
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_COMPONENT)]
    sys.modules[_PKG] = pkg
    for name in ("const", "schedule", "coordinator"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{name}", _COMPONENT / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules[f"{_PKG}.coordinator"]


coordinator = _load_coordinator()
DOMAIN = coordinator.DOMAIN


def test_only_disabled_devices_of_this_entry_are_returned():
    REGISTRY.devices = [
        _Device({(DOMAIN, "WRX1")}, {"entry_a"}, "user"),
        _Device({(DOMAIN, "WRX2")}, {"entry_a"}, None),
        _Device({(DOMAIN, "WRX3")}, {"entry_b"}, "user"),
    ]
    assert coordinator.disabled_serials(None, "entry_a") == {"WRX1": "user"}


def test_identifiers_of_other_integrations_are_ignored():
    REGISTRY.devices = [
        _Device({("other_domain", "WRX1"), (DOMAIN, "WRX9")}, {"entry_a"}, "user"),
    ]
    assert coordinator.disabled_serials(None, "entry_a") == {"WRX9": "user"}


def test_no_devices_means_nothing_disabled():
    REGISTRY.devices = []
    assert coordinator.disabled_serials(None, "entry_a") == {}
