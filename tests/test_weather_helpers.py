import importlib.util
import json
import pathlib

# Load weather_helpers DIRECTLY by file path. A normal
# `from custom_components.aiper_irrisense import weather_helpers` would execute
# the package __init__.py, which imports homeassistant (not installed in this
# test env). weather_helpers itself is pure stdlib, so file-loading it is clean.
_WH_PATH = (
    pathlib.Path(__file__).parents[1]
    / "custom_components" / "aiper_irrisense" / "weather_helpers.py"
)
_spec = importlib.util.spec_from_file_location("weather_helpers", _WH_PATH)
wh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wh)

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
