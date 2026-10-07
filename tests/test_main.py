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


@pytest.mark.parametrize("mode", ["trade", "validation"])
@pytest.mark.parametrize("conservative", [False, True])
@pytest.mark.parametrize("simplified", [False, True])
def test_main_runs_session_with_resolved_config(monkeypatch, mode, conservative, simplified):
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01")
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    config.mode = mode
    config.conservative_trading = conservative
    config.simplified_trading = simplified
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

    async def fake_run_sample_scenario(session, **kwargs):
        seen.update(kwargs)
        return session.world

    monkeypatch.setattr(main_module, "BazaarConnection", FakeConnection)
    monkeypatch.setattr(main_module, "open_session", fake_open_session)
    monkeypatch.setattr(main_module, "run_sample_scenario" if mode == "validation" else "run_trading", fake_run_sample_scenario)

    main_module.main()

    assert seen["config"] is config
    if mode == "trade":
        assert seen["conservative"] is conservative
        assert seen["simplified"] is simplified


def test_main_writes_html_summary_automatically_when_run_log_path_set(monkeypatch, tmp_path):
    run_log_path = tmp_path / "run.jsonl"
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01", run_log_path=str(run_log_path))
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    class FakeConnection:
        def __init__(self, passed_config):
            pass

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

    async def fake_run_trading(session, run_log=None, **_kwargs):
        if run_log:
            run_log.run_started(
                SimpleNamespace(
                    tick=0, run_id="r1", self_station_id="P01",
                    self=SimpleNamespace(specialty=SimpleNamespace(value="water"),
                                         inventory=SimpleNamespace(water=0, food=0, components=0),
                                         upkeep_per_tick=SimpleNamespace(water=0, food=0, components=0)),
                    rules=SimpleNamespace(duration_ticks=100, max_request_records_per_station=100),
                ),
                mode="trade",
            )
        return session.world

    monkeypatch.setattr(main_module, "BazaarConnection", FakeConnection)
    monkeypatch.setattr(main_module, "open_session", fake_open_session)
    monkeypatch.setattr(main_module, "run_trading", fake_run_trading)

    main_module.main()

    assert run_log_path.exists()
    summary_path = tmp_path / "run-summary.html"
    assert summary_path.exists()
    assert "<html" in summary_path.read_text(encoding="utf-8")


def test_main_passes_open_browser_through_to_run_log(monkeypatch, tmp_path):
    run_log_path = tmp_path / "run.jsonl"
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01",
                           run_log_path=str(run_log_path), open_browser=True)
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    class FakeConnection:
        def __init__(self, passed_config):
            pass

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

    async def fake_run_trading(session, run_log=None, **_kwargs):
        return session.world

    opened = []
    monkeypatch.setattr("planet_charlu.structured_log.webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(main_module, "BazaarConnection", FakeConnection)
    monkeypatch.setattr(main_module, "open_session", fake_open_session)
    monkeypatch.setattr(main_module, "run_trading", fake_run_trading)

    main_module.main()

    # No tick ever ran (fake_run_trading never touches run_log), so the
    # finally block's own refresh_html() call is this run's first (and
    # only) write -- and it must still honor open_browser.
    assert len(opened) == 1


def test_main_swallows_keyboard_interrupt(monkeypatch):
    config = ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01")
    monkeypatch.setattr(main_module, "load_config", lambda: config)

    def raise_keyboard_interrupt(_coro):
        _coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(main_module.asyncio, "run", raise_keyboard_interrupt)

    main_module.main()  # must not raise
