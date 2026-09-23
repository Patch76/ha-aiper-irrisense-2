"""Unit tests for dose-label parsing, including free (non-preset) values.

`const.py` is stdlib-only, so we file-load it directly.
"""
import importlib.util
import pathlib

import pytest

_CONST_PATH = (
    pathlib.Path(__file__).parents[1]
    / "custom_components" / "aiper_irrisense" / "const.py"
)
_spec = importlib.util.spec_from_file_location("const", _CONST_PATH)
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)

MM = 25.4


def test_presets_unchanged():
    assert const.parse_dose_label("3 mm") == ("waterYield", 0.1)
    assert const.parse_dose_label("6 mm") == ("waterYield", 0.25)
    assert const.parse_dose_label("13 mm") == ("waterYield", 0.5)
    assert const.parse_dose_label("1 min") == ("point_time", 1)
    assert const.parse_dose_label("5 min") == ("point_time", 5)
    assert const.parse_dose_label("10 min") == ("point_time", 10)


def test_free_mm_maps_to_inch():
    kind, val = const.parse_dose_label("18 mm")
    assert kind == "waterYield"
    assert val == pytest.approx(18 / MM)
    kind, val = const.parse_dose_label("23 mm")
    assert kind == "waterYield"
    assert val == pytest.approx(23 / MM)


def test_free_minutes():
    assert const.parse_dose_label("120 min") == ("point_time", 120)
    assert const.parse_dose_label("150 min") == ("point_time", 150)


def test_garbage_is_none():
    assert const.parse_dose_label("abc") is None
    assert const.parse_dose_label("") is None
    assert const.parse_dose_label("mm") is None
    assert const.parse_dose_label("5 mmm") is None
    assert const.parse_dose_label("min") is None


AREA, LINE, POINT = const.REGION_TYPE_AREA, const.REGION_TYPE_LINE, const.REGION_TYPE_POINT


@pytest.mark.parametrize("label", ["3 mm", "13 mm", "18 mm"])
def test_depth_fits_area_and_line_only(label):
    assert const.dose_fits_region_type(label, AREA)
    assert const.dose_fits_region_type(label, LINE)
    assert not const.dose_fits_region_type(label, POINT)


@pytest.mark.parametrize("label", ["1 min", "10 min", "120 min"])
def test_duration_fits_point_only(label):
    assert const.dose_fits_region_type(label, POINT)
    assert not const.dose_fits_region_type(label, AREA)
    assert not const.dose_fits_region_type(label, LINE)


@pytest.mark.parametrize("label", [None, "", "banana", "5 minutes"])
def test_garbage_fits_nothing(label):
    for rtype in (AREA, LINE, POINT):
        assert not const.dose_fits_region_type(label, rtype)


def test_label_amount():
    assert const.dose_label_amount("18 mm") == 18.0
    assert const.dose_label_amount("120 min") == 120.0
    assert const.dose_label_amount("3 mm") == 3.0
    assert const.dose_label_amount("banana") is None
    assert const.dose_label_amount(None) is None


def test_options_add_a_fitting_free_value():
    assert const.dose_options_for_region_type(AREA, "18 mm") == [
        "3 mm", "6 mm", "13 mm", "18 mm",
    ]
    assert const.dose_options_for_region_type(POINT, "120 min") == [
        "1 min", "5 min", "10 min", "120 min",
    ]


def test_options_ignore_presets_and_values_of_the_wrong_kind():
    presets_mm = ["3 mm", "6 mm", "13 mm"]
    assert const.dose_options_for_region_type(AREA, "6 mm") == presets_mm
    assert const.dose_options_for_region_type(AREA, "120 min") == presets_mm
    assert const.dose_options_for_region_type(POINT, "18 mm") == [
        "1 min", "5 min", "10 min",
    ]
    assert const.dose_options_for_region_type(AREA) == presets_mm
