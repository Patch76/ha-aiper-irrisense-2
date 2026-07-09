# Zone Map Image Entity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `image` entity per device rendering the watering-zone map as SVG with active-zone highlight and live head position, from data the coordinator already holds.

**Architecture:** A pure stdlib SVG renderer (`map_render.py`, no HA imports, unit-tested standalone) is consumed by a new `image` platform (`image.py`) whose entity bumps `image_last_updated` behind a change-key + 5 s throttle gate to avoid per-MQTT-frame recorder churn.

**Tech Stack:** Python stdlib only (no new manifest requirements). HA `ImageEntity` (`homeassistant.components.image`). pytest for the pure renderer.

**Spec:** `docs/superpowers/specs/2026-07-09-zone-map-image-design.md`

## Global Constraints

- Working directory: `/data/home/projects/aiper-irrisense/worktree/zone-map-image` (branch `feat/zone-map-image`, off v0.3.0 `992fc20`).
- `map_render.py` MUST NOT import anything outside the Python stdlib — tests file-load it directly (package `__init__.py` imports homeassistant, which is not installed in the test venv). Duplicate the region-type ints locally instead of importing `const.py`.
- No changes to `manifest.json` (weather-platform precedent: adding a platform needs only the `PLATFORMS` list entry).
- `content_type = "image/svg+xml"`. SVG client compatibility is an ASSUMPTION until the live gate (Task 4); the renderer stays swappable to Pillow-PNG behind the same entity.
- All text placed into SVG MUST go through `xml.sax.saxutils.escape` (zone names are cloud-supplied).
- Region `type`: 0=Area (polygon), 1=Line (polyline), 2=Point (circle). Device coords: head at origin (0,0), +Y "up" → flip Y for screen. `progress` arrives as 0..1 OR 0..100 (device-dependent) — normalize.
- Test venv (persistent, outside worktree): `/data/home/projects/aiper-irrisense/.venv-test`.
- Fixtures MUST be synthetic (generic English zone names). Real garden maps (`/data/home/projects/aiper-decompile/sample_maps/`) are for local live comparison only and must never be committed.
- Never push and never merge anything in this plan without explicit user approval (push is hook-gated anyway).

---

### Task 0: Test venv (one-time setup)

**Files:** none (environment only)

- [ ] **Step 1: Create venv + install pytest (skip if it exists)**

```bash
test -x /data/home/projects/aiper-irrisense/.venv-test/bin/pytest || {
  python3 -m venv /data/home/projects/aiper-irrisense/.venv-test
  /data/home/projects/aiper-irrisense/.venv-test/bin/pip install -q pytest
}
/data/home/projects/aiper-irrisense/.venv-test/bin/pytest --version
```

Expected: `pytest 8.x` version line.

---

### Task 1: `map_render.py` — static zone rendering

**Files:**
- Create: `custom_components/aiper_irrisense/map_render.py`
- Create: `tests/fixtures/zone_map_sample.json`
- Test: `tests/test_map_render.py`

**Interfaces:**
- Produces: `render_zone_map(regions: list[dict], active: dict | None = None) -> str` (SVG document string). Task 2 extends it; Task 3 calls it from the entity.
- Produces: module-level ints `REGION_TYPE_AREA = 0`, `REGION_TYPE_LINE = 1`, `REGION_TYPE_POINT = 2`.

- [ ] **Step 1: Write the synthetic fixture**

`tests/fixtures/zone_map_sample.json` — same shape as a real S3 zone map, generic names:

```json
{
  "regions": [
    {
      "flags": 2,
      "id": 3,
      "name": "Apple tree",
      "points": [
        {"rotate": 27666, "valve": 2645, "waterpress": 154.4, "x": -671.0, "y": 78.0}
      ],
      "type": 2
    },
    {
      "flags": 2,
      "id": 4,
      "name": "Rose & bed <north>",
      "points": [
        {"rotate": 30000, "valve": 2400, "waterpress": 120.0, "x": 100.0, "y": 400.0},
        {"rotate": 31000, "valve": 2400, "waterpress": 120.0, "x": 420.0, "y": 430.0},
        {"rotate": 32000, "valve": 2400, "waterpress": 120.0, "x": 400.0, "y": 120.0}
      ],
      "type": 0
    },
    {
      "flags": 2,
      "id": 7,
      "name": "Hedge line",
      "points": [
        {"rotate": 10000, "valve": 2500, "waterpress": 140.0, "x": -200.0, "y": -300.0},
        {"rotate": 11000, "valve": 2500, "waterpress": 140.0, "x": 250.0, "y": -350.0}
      ],
      "type": 1
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_map_render.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
/data/home/projects/aiper-irrisense/.venv-test/bin/python -m pytest tests/test_map_render.py -v
```

Expected: FAIL — `FileNotFoundError` / `AttributeError` (module missing).

- [ ] **Step 4: Implement `map_render.py`**

```python
"""Pure SVG renderer for the zone-map image entity.

Stdlib only — NO Home Assistant imports. Unit tests file-load this module
directly (the package __init__ pulls in homeassistant), so keep it standalone;
the region-type ints are duplicated from const.py on purpose.
"""
from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

REGION_TYPE_AREA = 0
REGION_TYPE_LINE = 1
REGION_TYPE_POINT = 2

# Quantisation grid for the live head position in render_key(); one unit is
# 1/3 inch, so 50 units ≈ 42 cm — coarse enough to suppress jitter bumps.
COORD_QUANT = 50.0

_BG = "#22303a"
_TEXT = "#eceff1"
_ZONE_STROKE = "#4dd0e1"
_ZONE_FILL = "rgba(77,208,225,0.18)"
_ACTIVE_STROKE = "#ffb74d"
_ACTIVE_FILL = "rgba(255,183,77,0.30)"
_HEAD = "#ffffff"


def _points(region: Any) -> list[tuple[float, float]]:
    """Usable (x, y) tuples of a region; [] when the shape is malformed."""
    if not isinstance(region, dict) or not isinstance(region.get("points"), list):
        return []
    out: list[tuple[float, float]] = []
    for p in region["points"]:
        if (
            isinstance(p, dict)
            and isinstance(p.get("x"), (int, float))
            and isinstance(p.get("y"), (int, float))
        ):
            out.append((float(p["x"]), float(p["y"])))
    return out


def _norm_progress(value: Any) -> int | None:
    """Device reports progress as 0..1 or 0..100 — normalize to int percent."""
    if not isinstance(value, (int, float)):
        return None
    pct = value * 100.0 if value <= 1.0 else value
    return max(0, min(100, int(pct)))


def render_key(regions: list[dict], active: dict | None) -> tuple:
    """Cheap change-detection key: only when this changes is a new image
    worth announcing (throttle gate lives in the entity)."""
    zone_id = progress = qx = qy = None
    if isinstance(active, dict) and active.get("is_running"):
        zone_id = active.get("zone_id")
        progress = _norm_progress(active.get("progress"))
        x, y = active.get("x"), active.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            qx = round(float(x) / COORD_QUANT)
            qy = round(float(y) / COORD_QUANT)
    geo = tuple(
        (r.get("id"), str(r.get("name")), r.get("type"), tuple(_points(r)))
        for r in (regions or [])
        if isinstance(r, dict)
    )
    return (geo, zone_id, progress, qx, qy)


def _placeholder() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300">'
        f'<rect width="400" height="300" fill="{_BG}"/>'
        f'<text x="200" y="150" fill="{_TEXT}" font-size="24" '
        'font-family="sans-serif" text-anchor="middle" '
        'dominant-baseline="middle">no map data</text></svg>'
    )


def render_zone_map(regions: list[dict], active: dict | None = None) -> str:
    """Render the zone map as an SVG document string.

    Device coordinates are Cartesian with the sprinkler head at the origin
    and +Y pointing "up"; SVG y grows downward, so y is emitted negated.
    Never raises on malformed region data — unusable regions are skipped and
    a placeholder is returned when nothing is renderable.
    """
    shaped = [(r, _points(r)) for r in (regions or []) if isinstance(r, dict)]
    shaped = [(r, pts) for r, pts in shaped if pts]
    if not shaped:
        return _placeholder()

    xs = [x for _, pts in shaped for x, _ in pts] + [0.0]
    ys = [-y for _, pts in shaped for _, y in pts] + [0.0]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = max(max_x - min_x, max_y - min_y, 100.0) * 0.12
    vb_x, vb_y = min_x - pad, min_y - pad
    vb_w, vb_h = (max_x - min_x) + 2 * pad, (max_y - min_y) + 2 * pad
    scale = max(vb_w, vb_h)
    stroke = scale * 0.006
    font = scale * 0.035
    dot = scale * 0.012

    running = bool(isinstance(active, dict) and active.get("is_running"))
    active_id = active.get("zone_id") if running else None

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{vb_x:.1f} {vb_y:.1f} {vb_w:.1f} {vb_h:.1f}">',
        f'<rect x="{vb_x:.1f}" y="{vb_y:.1f}" width="{vb_w:.1f}" '
        f'height="{vb_h:.1f}" fill="{_BG}"/>',
    ]

    for region, pts in shaped:
        is_active = region.get("id") == active_id and active_id is not None
        fill = _ACTIVE_FILL if is_active else _ZONE_FILL
        col = _ACTIVE_STROKE if is_active else _ZONE_STROKE
        rtype = region.get("type")
        spts = [(x, -y) for x, y in pts]
        coord_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in spts)
        if rtype == REGION_TYPE_AREA and len(spts) >= 3:
            parts.append(
                f'<polygon points="{coord_str}" fill="{fill}" '
                f'stroke="{col}" stroke-width="{stroke:.1f}"/>'
            )
        elif rtype == REGION_TYPE_LINE and len(spts) >= 2:
            parts.append(
                f'<polyline points="{coord_str}" fill="none" '
                f'stroke="{col}" stroke-width="{stroke * 2:.1f}" '
                'stroke-linecap="round"/>'
            )
        else:
            # Point zones and degenerate shapes: mark the first coordinate.
            x, y = spts[0]
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{dot * 2.5:.1f}" '
                f'fill="{fill}" stroke="{col}" stroke-width="{stroke:.1f}"/>'
            )
        lx, ly = spts[0]
        label = escape(str(region.get("name") or f"zone {region.get('id')}"))
        if is_active:
            pct = _norm_progress(active.get("progress")) if active else None
            if pct is not None:
                label = f"{label} · {pct}%"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly - dot * 3:.1f}" fill="{_TEXT}" '
            f'font-size="{font:.1f}" font-family="sans-serif" '
            'text-anchor="middle">' + label + "</text>"
        )

    # Sprinkler head at the device origin.
    parts.append(
        f'<circle cx="0" cy="0" r="{dot * 1.6:.1f}" fill="{_HEAD}"/>'
    )

    if running:
        x, y = active.get("x"), active.get("y")  # type: ignore[union-attr]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            sx, sy = float(x), -float(y)
            parts.append(
                f'<g class="spray">'
                f'<line x1="0" y1="0" x2="{sx:.1f}" y2="{sy:.1f}" '
                f'stroke="{_HEAD}" stroke-width="{stroke:.1f}" '
                f'stroke-dasharray="{stroke * 3:.1f}"/>'
                f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{dot:.1f}" '
                f'fill="{_ACTIVE_STROKE}"/></g>'
            )

    parts.append("</svg>")
    return "".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
/data/home/projects/aiper-irrisense/.venv-test/bin/python -m pytest tests/test_map_render.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
git add custom_components/aiper_irrisense/map_render.py tests/
git commit -m "feat: pure SVG renderer for zone maps"
```

---

### Task 2: `map_render.py` — live overlay + render key

**Files:**
- Modify: `custom_components/aiper_irrisense/map_render.py` (already complete from Task 1 — this task only adds the missing TESTS for the running path; if any fail, fix the renderer)
- Test: `tests/test_map_render.py` (append)

**Interfaces:**
- Consumes: `render_zone_map`, `render_key`, `COORD_QUANT` from Task 1.
- Produces: verified contract for Task 3: `render_key(regions, active) -> tuple` changes on zone switch / integer-percent tick / ≥50-unit head move, and is stable otherwise.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_map_render.py`:

```python
ACTIVE = {
    "is_running": True,
    "zone_id": 4,
    "progress": 0.37,   # 0..1 form
    "x": 210.0,
    "y": 300.0,
}


def test_running_render_highlights_active_zone_and_spray():
    svg = mr.render_zone_map(REGIONS, ACTIVE)
    assert 'class="spray"' in svg
    assert "37%" in svg              # 0..1 progress normalised to percent
    assert mr._ACTIVE_STROKE in svg  # active palette used


def test_running_render_flips_spray_y():
    svg = mr.render_zone_map(REGIONS, ACTIVE)
    assert 'y2="-300.0"' in svg


def test_render_key_stable_when_idle():
    k1 = mr.render_key(REGIONS, None)
    k2 = mr.render_key(REGIONS, {"is_running": False, "x": 1.0, "y": 2.0})
    assert k1 == k2


def test_render_key_ignores_sub_grid_jitter():
    a1 = dict(ACTIVE, x=210.0, y=300.0)
    a2 = dict(ACTIVE, x=214.0, y=296.0)  # < COORD_QUANT/2 movement
    assert mr.render_key(REGIONS, a1) == mr.render_key(REGIONS, a2)


def test_render_key_changes_on_zone_progress_and_big_move():
    base = mr.render_key(REGIONS, ACTIVE)
    assert mr.render_key(REGIONS, dict(ACTIVE, zone_id=3)) != base
    assert mr.render_key(REGIONS, dict(ACTIVE, progress=0.42)) != base
    assert mr.render_key(REGIONS, dict(ACTIVE, x=210.0 + 2 * mr.COORD_QUANT)) != base


def test_render_key_changes_when_map_changes():
    other = [dict(r, name=str(r.get("name")) + "!") for r in REGIONS]
    assert mr.render_key(other, None) != mr.render_key(REGIONS, None)
```

- [ ] **Step 2: Run the new tests**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
/data/home/projects/aiper-irrisense/.venv-test/bin/python -m pytest tests/test_map_render.py -v
```

Expected: all 12 pass (Task 1 already implemented the running path). If any of the new tests FAIL, fix `map_render.py` minimally until green — the tests, not the Task 1 code, are authoritative for the contract.

- [ ] **Step 3: Commit**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
git add tests/test_map_render.py
git commit -m "test: cover live overlay and render-key throttling contract"
```

---

### Task 3: `image.py` platform + registration

**Files:**
- Create: `custom_components/aiper_irrisense/image.py`
- Modify: `custom_components/aiper_irrisense/__init__.py:27-33` (PLATFORMS list)

**Interfaces:**
- Consumes: `render_zone_map`, `render_key` (Tasks 1–2); `IrrisenseEntity(coordinator, sn, key)` (`entity.py:18`); `coordinator.zones_for(sn)` (`coordinator.py:1134`), `coordinator.active_zone_state(sn)` (`coordinator.py:771`); `ImageEntity.__init__(self, hass, verify_ssl=False)`.
- Produces: `image.<device>_zone_map` entity, unique_id `{sn}_zone_map`.

No unit test — the entity needs a running HA; verification is `py_compile` here and the live gates in Task 4.

- [ ] **Step 1: Write `image.py`**

```python
"""Zone-map image platform for the Aiper Irrisense 2.

Renders the cached S3 zone map (plus live run state) as SVG. The heavy
lifting lives in map_render.py (pure, unit-tested); this module only wires
it to an ImageEntity with a throttled image_last_updated bump.
"""
from __future__ import annotations

import time

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import IrrisenseCoordinator
from .entity import IrrisenseEntity
from .map_render import render_key, render_zone_map

# Floor between image_last_updated bumps. realTimeProgress frames arrive
# every ~1-2 s during a run; every bump costs a recorder state row plus a
# frontend refetch, so material changes are announced at most once per
# window. A skipped change is picked up by the next frame or REST poll.
MIN_BUMP_INTERVAL = 5.0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrisenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        ZoneMapImage(hass, coordinator, sn)
        for dev in coordinator.devices
        if (sn := dev.get("sn"))
    ]
    async_add_entities(entities)


class ZoneMapImage(IrrisenseEntity, ImageEntity):
    """SVG map of the device's watering zones with live run overlay."""

    _attr_content_type = "image/svg+xml"
    _attr_name = "Zone map"

    def __init__(
        self, hass: HomeAssistant, coordinator: IrrisenseCoordinator, sn: str
    ) -> None:
        IrrisenseEntity.__init__(self, coordinator, sn, "zone_map")
        ImageEntity.__init__(self, hass)
        self._render_state = self._current_render_state()
        self._last_key = render_key(*self._render_state)
        self._last_bump = time.monotonic()
        self._attr_image_last_updated = dt_util.utcnow()

    def _current_render_state(self) -> tuple[list[dict], dict | None]:
        return (
            self.coordinator.zones_for(self._sn),
            self.coordinator.active_zone_state(self._sn),
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        state = self._current_render_state()
        key = render_key(*state)
        now = time.monotonic()
        if key != self._last_key and (now - self._last_bump) >= MIN_BUMP_INTERVAL:
            self._render_state = state
            self._last_key = key
            self._last_bump = now
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        # Render from the last ANNOUNCED state (not the live one) so the
        # served image always matches the image_last_updated the frontend
        # cached against.
        regions, active = self._render_state
        try:
            return render_zone_map(regions, active).encode("utf-8")
        except Exception:  # noqa: BLE001 - a broken frame must not 500 the image view
            return render_zone_map([], None).encode("utf-8")
```

- [ ] **Step 2: Register the platform**

In `custom_components/aiper_irrisense/__init__.py`, extend the list at line 27:

```python
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.IMAGE,
]
```

- [ ] **Step 3: Syntax check (no HA in venv, so compile only)**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
python3 -m py_compile custom_components/aiper_irrisense/image.py custom_components/aiper_irrisense/__init__.py && echo OK
```

Expected: `OK`. Also re-run the renderer tests (unchanged, must stay green):

```bash
/data/home/projects/aiper-irrisense/.venv-test/bin/python -m pytest tests/ -v
```

Expected: 12 passed.

- [ ] **Step 4: Commit**

```bash
cd /data/home/projects/aiper-irrisense/worktree/zone-map-image
git add custom_components/aiper_irrisense/image.py custom_components/aiper_irrisense/__init__.py
git commit -m "feat: zone-map image entity with throttled live updates"
```

---

### Task 4: LB deploy + live gates (USER-GATED)

**Files:** none in this worktree (dev-combined rebuild + fork push)

**Interfaces:**
- Consumes: the finished `feat/zone-map-image` branch.
- Produces: pass/fail on the spec's acceptance gates; on SVG failure → follow-up task "Pillow-PNG fallback renderer".

Every step below that pushes, restarts HA, or starts watering requires an explicit verb+object "ja" from the user first. Do not batch them.

- [ ] **Step 1: Rebuild `dev-combined`** — reset `worktree/dev-combined` to `upstream/main`, re-merge all open PR branches (#24 `chore/remove-unused-imports-f401`, #25 `add-wcx-serial-prefix`, #26 `chore/drop-stale-water-pressure-chip`, #28 `fix/switch-write-payloads`, #32 `feat/weather-entity`, #36 `fix/zone-map-retry-backoff`, #40 `feat/experimental-pesticide-skip-sensors`, #42 `feat/derived-live-sensors`, #43 `fix/relogin-on-account-conflict`) **plus `feat/zone-map-image`**. Known conflicts: the 4 additive hunks in `const.py` / `config_flow.py` / `coordinator.py` (×2) / `translations/en.json` — keep both sides; `feat/zone-map-image` adds a 5th additive hunk in `__init__.py` PLATFORMS (keep all platform entries, `Platform.IMAGE` last).
- [ ] **Step 2: Push fork main** (gh api Git-Data commit, base_tree + blobs — ask user first).
- [ ] **Step 3: USER: HACS Redownload + full HA restart** (code changes need the full restart, not a reload).
- [ ] **Step 4: Idle gate** — `image.sprenger_zone_map` + `image.sprenger_2_zone_map` exist and render the zone layout in a **desktop browser** and in the **Companion app** (SVG compatibility gate). Verify via LB MCP (`mcp__claude_ai_LB__*`): entity states are timestamps, no `aiper` errors in the log.
- [ ] **Step 5: Live gate (physical watering — user consent)** — 1-minute run on one zone: active zone highlights, spray marker moves, progress label updates roughly every 5 s.
- [ ] **Step 6: Churn gate** — after the run, count recorder rows: history of the image entity over the run window must show ≤ ~1 row / 5 s (use `ha_get_history` on the image entity).
- [ ] **Step 7: On SVG failure only** — swap `map_render.py` internals to a Pillow-PNG renderer behind the same entity (`content_type = "image/png"`); Pillow ships with HA core images. New plan task if triggered.

---

## Self-Review (done at write time)

- **Spec coverage:** renderer + escape + idle/empty/malformed paths (Task 1), overlay + throttle contract (Task 2), entity + gate + registration (Task 3), live/churn/SVG gates + PNG fallback pointer (Task 4). Availability-tracks-coordinator comes free via `IrrisenseEntity.available`.
- **Placeholders:** none — all code complete.
- **Type consistency:** `render_zone_map(regions, active=None) -> str`, `render_key(regions, active) -> tuple` used identically in Tasks 1–3; `IrrisenseEntity.__init__(coordinator, sn, key)` and `ImageEntity.__init__(hass)` match verified signatures (`entity.py:18`, HA core `image/__init__.py`).
