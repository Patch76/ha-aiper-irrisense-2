import importlib.util
import json
import pathlib

# Load map_render DIRECTLY by file path. A normal
# `from custom_components.aiper_irrisense import map_render` would execute the
# package __init__.py, which imports homeassistant (not installed in this test
# env). map_render itself is pure stdlib, so file-loading it is clean.
_MR_PATH = (
    pathlib.Path(__file__).parents[1]
    / "custom_components" / "aiper_irrisense" / "map_render.py"
)
_spec = importlib.util.spec_from_file_location("map_render", _MR_PATH)
mr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mr)

FIX = pathlib.Path(__file__).parent / "fixtures" / "zone_map_sample.json"
REGIONS = json.loads(FIX.read_text())["regions"]


def test_idle_render_contains_all_zone_shapes():
    svg = mr.render_zone_map(REGIONS)
    assert svg.startswith("<svg")
    assert svg.count("<polygon") == 1   # type 0 (Area)
    assert svg.count("<polyline") == 1  # type 1 (Line)
    # type 2 (Point) circle + head-origin marker
    assert svg.count("<circle") >= 2


def test_zone_names_are_xml_escaped():
    svg = mr.render_zone_map(REGIONS)
    assert "Rose &amp; bed &lt;north&gt;" in svg
    assert "bed <north>" not in svg


def test_idle_render_has_no_spray_overlay():
    svg = mr.render_zone_map(REGIONS)
    assert 'class="spray"' not in svg


def test_empty_regions_renders_placeholder():
    for empty in ([], None):
        svg = mr.render_zone_map(empty or [])
        assert "no map data" in svg
        assert svg.startswith("<svg")


def test_malformed_region_is_skipped_without_raising():
    bad = REGIONS + [
        {"id": 99, "name": "broken", "type": 0, "points": "not-a-list"},
        {"id": 98, "name": "empty", "type": 0, "points": []},
        "not-a-dict",
    ]
    svg = mr.render_zone_map(bad)
    assert svg.count("<polygon") == 1  # still only the one valid Area


def test_y_axis_is_flipped():
    # Fixture point zone sits at y=+78 (device coords, +Y up). On screen it
    # must land ABOVE the origin marker => smaller SVG y than the origin's.
    svg = mr.render_zone_map(REGIONS)
    assert 'cy="-78.0"' in svg  # emitted as flipped device coordinate
