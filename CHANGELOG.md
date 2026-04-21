# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — Initial public release

First public release. The integration has been iterated on privately; this
snapshot is the cleaned-up baseline from which future changes will be tracked.

### Features

- Cloud-polled control of Aiper Irrisense 2 devices via MQTT over AWS IoT.
- Per-device entities: active zone, progress %, coverage passes, elapsed /
  remaining seconds, water pressure, rain-sensing state, firmware versions,
  Wi-Fi signal, lifetime water totals.
- Start / stop watering buttons plus a shape-shifting Dose / Duration select
  that adapts to the currently-selected zone's region type (Area / Line /
  Point).
- Progress-spike filter in the coordinator to suppress transient 0→100→low
  blips from the device's `realTimeProgress` stream.
- Three example Lovelace dashboards (single-device, dual-device, and a
  side-by-side alternative) under `examples/`.
