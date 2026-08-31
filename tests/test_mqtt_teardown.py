"""Unit tests for MQTT client teardown and crash-shield recovery.

`api.py` uses relative imports but pulls in nothing from Home Assistant, so we
assemble a throwaway package around it rather than importing the integration
(whose `__init__.py` does need HA).
"""
import importlib.util
import pathlib
import sys
import threading
import types

import pytest

_COMPONENT = (
    pathlib.Path(__file__).parents[1] / "custom_components" / "aiper_irrisense"
)
_PKG = "aiper_api_under_test"


def _load_api():
    if _PKG in sys.modules:
        return sys.modules[f"{_PKG}.api"]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_COMPONENT)]
    sys.modules[_PKG] = pkg
    for name in ("const", "crypto", "api"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{name}", _COMPONENT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{_PKG}.api"]


api_mod = _load_api()


# --------------------------------------------------------------------------- #
# Fakes shaped like the AWSIoTPythonSDK object graph
# --------------------------------------------------------------------------- #


class FakeConsumer:
    def __init__(self):
        self.running = True
        self.stop_calls = 0

    def is_running(self):
        return self.running

    def stop(self):
        self.stop_calls += 1
        self.running = False

    def wait_until_it_stops(self, timeout):
        return True


class FakePaho:
    def __init__(self, thread):
        self._thread = thread


class FakeInternalClient:
    def __init__(self, thread):
        self._paho_client = FakePaho(thread)


class FakeCore:
    def __init__(self, thread):
        self._internal_async_client = FakeInternalClient(thread)
        self._event_consumer = FakeConsumer()


class FakeClient:
    """Stands in for AWSIoTMQTTClient.

    `disconnect_ok=False` reproduces the real behaviour on a client whose loop
    thread can no longer deliver the DISCONNECT event.
    """

    def __init__(self, thread, disconnect_ok=True):
        self._mqtt_core = FakeCore(thread)
        self._disconnect_ok = disconnect_ok
        self.disconnect_calls = 0
        self.timeouts = []

    def configureConnectDisconnectTimeout(self, seconds):  # noqa: N802 - SDK name
        self.timeouts.append(seconds)

    def disconnect(self):
        self.disconnect_calls += 1
        if not self._disconnect_ok:
            raise RuntimeError("disconnectTimeoutException")
        self._mqtt_core._event_consumer.stop()
        return True


def _dead_thread():
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    return t


def _live_thread(stop_event):
    t = threading.Thread(target=stop_event.wait, daemon=True)
    t.start()
    return t


@pytest.fixture
def client_api():
    return api_mod.IrrisenseApi("user@example.invalid", "pw")


# --------------------------------------------------------------------------- #
# _force_close_mqtt_client
# --------------------------------------------------------------------------- #


def test_dead_loop_thread_stops_event_consumer_without_disconnect(client_api):
    """The leak: a crashed client's dispatch thread must not survive teardown."""
    client = FakeClient(_dead_thread())

    client_api._force_close_mqtt_client(client)

    consumer = client._mqtt_core._event_consumer
    assert consumer.stop_calls == 1
    assert not consumer.is_running()
    # disconnect() on a dead loop thread can only burn the disconnect timeout.
    assert client.disconnect_calls == 0


def test_live_loop_thread_disconnects_gracefully(client_api):
    stop = threading.Event()
    try:
        client = FakeClient(_live_thread(stop))

        client_api._force_close_mqtt_client(client)

        assert client.disconnect_calls == 1
        assert client.timeouts == [5]
        assert not client._mqtt_core._event_consumer.is_running()
    finally:
        stop.set()


def test_failed_disconnect_still_stops_event_consumer(client_api):
    stop = threading.Event()
    try:
        client = FakeClient(_live_thread(stop), disconnect_ok=False)

        client_api._force_close_mqtt_client(client)

        assert client.disconnect_calls == 1
        assert client._mqtt_core._event_consumer.stop_calls == 1
    finally:
        stop.set()


def test_teardown_survives_unknown_sdk_internals(client_api):
    class Opaque:
        pass

    client_api._force_close_mqtt_client(Opaque())  # must not raise


# --------------------------------------------------------------------------- #
# disconnect()
# --------------------------------------------------------------------------- #


def test_disconnect_tears_down_even_when_not_connected(client_api):
    """After an eviction `_mqtt_connected` is False while the SDK threads live."""
    client = FakeClient(_dead_thread())
    client_api._mqtt_client = client
    client_api._mqtt_connected = False

    client_api.disconnect()

    assert client._mqtt_core._event_consumer.stop_calls == 1
    assert client_api._mqtt_client is None


def test_disconnect_blocks_further_mqtt_connects(client_api, monkeypatch):
    # Needs the SDK importable, otherwise connect_mqtt() bails on the import
    # and the assertion below can't tell the shutdown guard from that bail-out.
    pytest.importorskip("AWSIoTPythonSDK.MQTTLib")
    client_api._identity_id = "id"
    client_api._iot_endpoint = "endpoint.invalid"
    reached = []
    monkeypatch.setattr(
        client_api, "_get_aws_credentials", lambda: reached.append(1)
    )

    client_api.disconnect()

    assert client_api.connect_mqtt() is False
    assert reached == [], "shutdown must short-circuit before building a client"


# --------------------------------------------------------------------------- #
# excepthook dispatch
# --------------------------------------------------------------------------- #


def test_crash_is_dispatched_to_the_instance_owning_the_thread():
    first = api_mod.IrrisenseApi("a@example.invalid", "pw")
    second = api_mod.IrrisenseApi("b@example.invalid", "pw")
    stop = threading.Event()
    try:
        first._mqtt_client = FakeClient(_live_thread(stop))
        dying = _dead_thread()
        second._mqtt_client = FakeClient(dying)

        assert second._owns_loop_thread(dying) is True
        assert first._owns_loop_thread(dying) is False
    finally:
        stop.set()


def test_owns_loop_thread_is_false_without_a_client(client_api):
    assert client_api._owns_loop_thread(_dead_thread()) is False
    assert client_api._owns_loop_thread(None) is False


# --------------------------------------------------------------------------- #
# crash-shield retry
# --------------------------------------------------------------------------- #


def test_crash_shield_retries_until_it_connects(client_api, monkeypatch):
    monkeypatch.setattr(api_mod, "_CRASH_SHIELD_BACKOFF_SECONDS", (0.01,))
    attempts = []

    def fake_connect():
        attempts.append(1)
        return len(attempts) >= 3

    monkeypatch.setattr(client_api, "connect_mqtt", fake_connect)
    client_api._mqtt_client = FakeClient(_dead_thread())
    client_api._reconnecting = True

    client_api._spawn_crash_shield_worker()
    deadline = threading.Event()
    deadline.wait(3.0)

    assert len(attempts) == 3, "a failed reconnect must not end the recovery"
    assert client_api._reconnecting is False


def test_crash_shield_stops_on_shutdown(client_api, monkeypatch):
    monkeypatch.setattr(api_mod, "_CRASH_SHIELD_BACKOFF_SECONDS", (0.05,))
    attempts = []
    monkeypatch.setattr(
        client_api, "connect_mqtt", lambda: (attempts.append(1), False)[1]
    )
    client_api._reconnecting = True

    client_api._spawn_crash_shield_worker()
    client_api._shutdown.set()
    threading.Event().wait(1.0)

    assert len(attempts) <= 1, "shutdown must stop the retry loop"
