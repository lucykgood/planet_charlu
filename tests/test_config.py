import json

import pytest

from planet_charlu.config import ClientConfig, ConfigError, load_config


def test_defaults_apply_when_nothing_else_given():
    config = load_config(argv=["--token", "inline-token"], env={})

    assert config.ws_url == "wss://spaceport.edneo.com/ws"
    assert config.station_id == "P01"
    assert config.token == "inline-token"
    assert config.verbose is False


def test_verbose_flag():
    assert load_config(["--token", "t"], {}).verbose is False
    assert load_config(["--token", "t", "--verbose"], {}).verbose is True
    assert load_config(["--token", "t", "-v"], {}).verbose is True


def test_open_browser_defaults_to_true_via_cli_but_false_on_direct_construction():
    assert load_config(["--token", "t"], {}).open_browser is True
    assert load_config(["--token", "t", "--no-open-browser"], {}).open_browser is False
    # Bypassing the CLI (as tests that construct ClientConfig directly do)
    # must never open a browser as a side effect.
    assert ClientConfig(ws_url="ws://x/ws", token="t", station_id="P01").open_browser is False


def test_run_log_path_defaults_to_an_auto_named_file_under_runs():
    config = load_config(["--token", "t", "--station-id", "P07"], {})

    assert config.run_log_path is not None
    assert config.run_log_path.startswith("runs/P07-")
    assert config.run_log_path.endswith(".jsonl")


def test_run_log_path_override_is_respected():
    config = load_config(["--token", "t", "--run-log", "custom/path.jsonl"], {})

    assert config.run_log_path == "custom/path.jsonl"


def test_env_vars_override_defaults():
    env = {
        "BAZAAR_WS_URL": "ws://example.test:9000/ws",
        "BAZAAR_TOKEN": "env-token",
        "BAZAAR_STATION_ID": "P05",
    }

    config = load_config(argv=[], env=env)

    assert config.ws_url == "ws://example.test:9000/ws"
    assert config.token == "env-token"
    assert config.station_id == "P05"


def test_cli_flags_override_env_vars():
    env = {"BAZAAR_WS_URL": "ws://example.test:9000/ws", "BAZAAR_TOKEN": "env-token"}

    config = load_config(argv=["--ws-url", "ws://override.test:1234/ws"], env=env)

    assert config.ws_url == "ws://override.test:1234/ws"
    assert config.token == "env-token"


def test_token_read_from_credentials_file_when_not_given_directly(tmp_path):
    credentials_path = tmp_path / "validation-credentials.json"
    credentials_path.write_text(
        json.dumps(
            {
                "players": [
                    {"station_id": "P02", "token": "wrong-station"},
                    {"station_id": "P01", "token": "file-token"},
                ]
            }
        )
    )

    config = load_config(
        argv=["--credentials-file", str(credentials_path)], env={}
    )

    assert config.token == "file-token"


def test_missing_credentials_file_raises_config_error(tmp_path):
    missing_path = tmp_path / "does-not-exist.json"

    with pytest.raises(ConfigError, match="not found"):
        load_config(argv=["--credentials-file", str(missing_path)], env={})


def test_credentials_file_with_empty_token_raises_config_error(tmp_path):
    credentials_path = tmp_path / "validation-credentials.json"
    credentials_path.write_text(
        json.dumps({"players": [{"station_id": "P01", "token": ""}]})
    )

    with pytest.raises(ConfigError, match="has no token"):
        load_config(argv=["--credentials-file", str(credentials_path)], env={})


def test_credentials_file_without_matching_station_raises_config_error(tmp_path):
    credentials_path = tmp_path / "validation-credentials.json"
    credentials_path.write_text(json.dumps({"players": [{"station_id": "P02", "token": "t"}]}))

    with pytest.raises(ConfigError, match="P01"):
        load_config(argv=["--credentials-file", str(credentials_path)], env={})


def test_malformed_credentials_file_raises_config_error(tmp_path):
    credentials_path = tmp_path / "validation-credentials.json"
    credentials_path.write_text("not valid json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(argv=["--credentials-file", str(credentials_path)], env={})


def test_config_repr_and_str_redact_token():
    config = ClientConfig(ws_url="ws://x/ws", token="super-secret", station_id="P01")

    assert "super-secret" not in repr(config)
    assert "super-secret" not in str(config)
    assert "redacted" in repr(config)


def test_conservative_trading_flag_and_environment():
    assert not load_config(["--token", "t"], {}).conservative_trading
    assert load_config(["--token", "t", "--conservative-trading"], {}).conservative_trading
    env = {"BAZAAR_TOKEN": "t", "BAZAAR_CONSERVATIVE_TRADING": "true"}
    assert load_config([], env).conservative_trading
    assert not load_config(["--no-conservative-trading"], env).conservative_trading
