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
