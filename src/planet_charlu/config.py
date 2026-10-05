"""Runtime configuration: endpoint and credentials, without source edits.

Resolution order for each setting is CLI flag, then environment variable,
then a built-in default. The token itself is never read from a CLI flag's
default or logged; ``ClientConfig`` redacts it from ``repr``/``str`` so an
accidental ``logger.info("%r", config)`` cannot leak it.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_WS_URL = "wss://spaceport.edneo.com/ws"
DEFAULT_STATION_ID = "P01"
DEFAULT_CREDENTIALS_FILE = "validation-credentials.json"
DEFAULT_RUN_LOG_DIR = "runs"


class ConfigError(RuntimeError):
    """Raised when configuration cannot be resolved into a usable client."""


@dataclass
class ClientConfig:
    ws_url: str
    token: str
    station_id: str
    mode: str = "trade"
    run_log_path: str | None = None
    open_browser: bool = False
    verbose: bool = False
    conservative_trading: bool = False

    def __repr__(self) -> str:
        return (
            f"ClientConfig(ws_url={self.ws_url!r}, station_id={self.station_id!r}, "
            f"run_log_path={self.run_log_path!r}, open_browser={self.open_browser!r}, "
            f"token='***redacted***')"
        )

    __str__ = __repr__


def _token_from_credentials_file(path: Path, station_id: str) -> str:
    try:
        raw = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(
            f"no --token given and credentials file not found: {path}"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"credentials file is not valid JSON: {path}") from exc

    for player in data.get("players", []):
        if player.get("station_id") == station_id:
            token = player.get("token")
            if not token:
                raise ConfigError(
                    f"credentials entry for station_id={station_id!r} has no token"
                )
            return token

    raise ConfigError(
        f"no players entry for station_id={station_id!r} in {path}"
    )


def _build_parser(env: Mapping[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Planet CharLu Bazaar client")
    parser.add_argument(
        "--ws-url",
        default=env.get("BAZAAR_WS_URL", DEFAULT_WS_URL),
        help="WebSocket endpoint, e.g. ws://127.0.0.1:3001/ws (env: BAZAAR_WS_URL)",
    )
    parser.add_argument(
        "--token",
        default=env.get("BAZAAR_TOKEN"),
        help="Bearer token; falls back to --credentials-file if omitted (env: BAZAAR_TOKEN)",
    )
    parser.add_argument(
        "--credentials-file",
        default=env.get("BAZAAR_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE),
        help="validation-credentials.json path to read a token from (env: BAZAAR_CREDENTIALS_FILE)",
    )
    parser.add_argument(
        "--station-id",
        default=env.get("BAZAAR_STATION_ID", DEFAULT_STATION_ID),
        help="Station ID to select from the credentials file (env: BAZAAR_STATION_ID)",
    )
    parser.add_argument("--mode", choices=("trade", "validation"), default="trade",
                        help="Continuous self-sufficient trading (default) or the validator exercise")
    parser.add_argument(
        "--run-log",
        default=env.get("BAZAAR_RUN_LOG"),
        dest="run_log_path",
        help="Path to append a structured JSON-Lines run log to. An HTML summary "
             "(scripts/generate_run_summary.py) is generated automatically from it "
             f"when the run ends. Defaults to an auto-named file under {DEFAULT_RUN_LOG_DIR}/ "
             "(env: BAZAAR_RUN_LOG)",
    )
    parser.add_argument(
        "--no-open-browser", dest="open_browser", action="store_false", default=True,
        help="Don't automatically open the HTML run summary in a browser after its first "
             "write. On by default; turn it off in a headless environment (e.g. Docker) "
             "where there is no browser to open.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Also log raw per-message protocol detail (DEBUG level); the default "
             "INFO level shows the per-tick dashboard and per-decision lines only",
    )
    parser.add_argument(
        "--conservative-trading", action=argparse.BooleanOptionalAction,
        default=env.get("BAZAAR_CONSERVATIVE_TRADING", "false").lower() in ("true", "1", "yes"),
        help="Target 15 ticks of supply and gift only for advertised resource emergencies "
             "(env: BAZAAR_CONSERVATIVE_TRADING)",
    )
    return parser


def load_config(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> ClientConfig:
    """Resolve a ``ClientConfig`` from CLI args, environment variables, and defaults.

    Changing server, port, or credentials never requires a source edit: pass
    ``--ws-url``/``--token``/``--credentials-file``/``--station-id``, or set
    ``BAZAAR_WS_URL``/``BAZAAR_TOKEN``/``BAZAAR_CREDENTIALS_FILE``/``BAZAAR_STATION_ID``.
    """
    resolved_env = os.environ if env is None else env
    args = _build_parser(resolved_env).parse_args(argv)

    token = args.token
    if not token:
        token = _token_from_credentials_file(Path(args.credentials_file), args.station_id)

    run_log_path = args.run_log_path or _default_run_log_path(args.station_id)

    return ClientConfig(
        ws_url=args.ws_url,
        token=token,
        station_id=args.station_id,
        mode=args.mode,
        run_log_path=run_log_path,
        open_browser=args.open_browser,
        verbose=args.verbose,
        conservative_trading=args.conservative_trading,
    )


def _default_run_log_path(station_id: str) -> str:
    """An auto-named path so every run gets a structured log and HTML summary
    without needing ``--run-log`` -- see ``main.py``'s automatic write_summary
    call and docs/task7-structured-log-proposal.md.
    """
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{DEFAULT_RUN_LOG_DIR}/{station_id}-{timestamp}.jsonl"
