"""Unit tests for the coordinator's zone / dose selection.

`coordinator.py` imports Home Assistant, which the test environment does not
install. Its few HA imports are stubbed and `api.py` (network, crypto) is
replaced, so the selection logic runs on a bare coordinator instance.
"""
import importlib.util
import pathlib
import sys
import types

import pytest

_COMPONENT = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "aiper_irrisense"
)
_PKG = "aiper_selection_under_test"
SENT: list = []


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
    _stub("homeassistant.helpers.device_registry")
    _stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=None)
    _stub(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_Coordinator,
        UpdateFailed=Exception,
    )
    _stub(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_send=lambda hass, signal, *args: SENT.append((signal, args)),
    )
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
SN = "SN1"
AREA, LINE, POINT = 1, 2, 3
REGIONS = [{"id": AREA, "type": 0}, {"id": LINE, "type": 1}, {"id": POINT, "type": 2}]


@pytest.fixture
def coord():
    SENT.clear()
    c = object.__new__(coordinator.IrrisenseCoordinator)
    c.hass = None
    c._zone_selection = {}
    c._dose_selection = {}
    c._data = {SN: {"map": {"regions": [dict(r) for r in REGIONS]}}}
    return c


def test_depth_carries_over_between_area_and_line(coord):
    coord.set_zone_selection(SN, AREA)
    assert coord.set_dose_selection(SN, "18 mm")
    coord.set_zone_selection(SN, LINE)
    assert coord.get_dose_selection(SN) == "18 mm"


def test_point_zone_shows_its_default_and_depth_comes_back(coord):
    coord.set_zone_selection(SN, AREA)
    coord.set_dose_selection(SN, "18 mm")
    coord.set_zone_selection(SN, POINT)
    assert coord.get_dose_selection(SN) == "1 min"
    coord.set_zone_selection(SN, AREA)
    assert coord.get_dose_selection(SN) == "18 mm"


def test_wrong_kind_is_refused_without_signal(coord):
    coord.set_zone_selection(SN, AREA)
    coord.set_dose_selection(SN, "13 mm")
    SENT.clear()
    assert coord.set_dose_selection(SN, "120 min") is False
    assert coord.get_dose_selection(SN) == "13 mm"
    assert SENT == []


def test_accepted_dose_sends_the_signal_once(coord):
    coord.set_zone_selection(SN, POINT)
    SENT.clear()
    assert coord.set_dose_selection(SN, "120 min") is True
    assert SENT == [(coordinator.SIGNAL_SELECTION_CHANGED, (SN,))]


def test_nothing_picked_returns_the_type_default(coord):
    coord.set_zone_selection(SN, POINT)
    assert coord.get_dose_selection(SN) == "1 min"
    coord.set_zone_selection(SN, AREA)
    assert coord.get_dose_selection(SN) == "3 mm"


def test_map_change_of_zone_type_masks_the_stale_dose(coord):
    coord.set_zone_selection(SN, AREA)
    coord.set_dose_selection(SN, "18 mm")
    coord._data[SN]["map"]["regions"][0]["type"] = 2  # zone became a Point
    assert coord.get_dose_selection(SN) == "1 min"


def test_restore_before_the_map_is_loaded_keeps_the_dose(coord):
    regions = coord._data[SN]["map"]["regions"]
    coord._data = {}
    assert coord.set_dose_selection(SN, "120 min") is True
    coord._data = {SN: {"map": {"regions": regions}}}
    coord.set_zone_selection(SN, POINT)
    assert coord.get_dose_selection(SN) == "120 min"
