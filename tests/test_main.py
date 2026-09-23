from types import SimpleNamespace

import pytest

from planet_charlu import main as main_module
from planet_charlu.config import ClientConfig, ConfigError


def test_main_exits_with_status_1_on_config_error(monkeypatch):
    def raise_config_error(*args, **kwargs):
        raise ConfigError("no token available")

    monkeypatch.setattr(main_module, "load_config", raise_config_error)

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1


def test_main_runs_session_with_resolved_config(monkeypatch):
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01")
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    seen = {}

    class FakeConnection:
        def __init__(self, passed_config):
            seen["config"] = passed_config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    class FakeSession:
        world = SimpleNamespace(self=SimpleNamespace(inventory=None), transactions=[])

        async def stop_pump(self):
            pass

    async def fake_open_session(connection):
        return FakeSession()

    async def fake_run_sample_scenario(session):
        return session.world

    monkeypatch.setattr(main_module, "BazaarConnection", FakeConnection)
    monkeypatch.setattr(main_module, "open_session", fake_open_session)
    monkeypatch.setattr(main_module, "run_sample_scenario", fake_run_sample_scenario)

    main_module.main()

    assert seen["config"] is config


def test_main_swallows_keyboard_interrupt(monkeypatch):
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01")
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    def raise_keyboard_interrupt(_coro):
        _coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module.asyncio, "run", raise_keyboard_interrupt)

    main_module.main()  # must not raise
