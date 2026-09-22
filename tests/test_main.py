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

    async def fake_run(passed_config):
        seen["config"] = passed_config

    monkeypatch.setattr(main_module.session, "run", fake_run)

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
