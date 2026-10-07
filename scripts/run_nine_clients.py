"""Launch nine conservative clients with nine distinct server credentials."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from planet_charlu.config import DEFAULT_WS_URL


def read_players(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    players = data.get("players") if isinstance(data, dict) else None
    if not isinstance(players, list) or len(players) != 9:
        raise ValueError("credentials must contain exactly nine players")
    stations, tokens = set(), set()
    for player in players:
        if not isinstance(player, dict):
            raise ValueError("each player must have station_id and token")
        station, token = player.get("station_id"), player.get("token")
        if not isinstance(station, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", station):
            raise ValueError("station IDs must contain only letters, digits, underscores, or hyphens")
        if not isinstance(token, str) or not token.strip() or token != token.strip() or token.startswith("REPLACE_"):
            raise ValueError("replace every token placeholder with its API key, without surrounding whitespace")
        if station in stations or token in tokens:
            raise ValueError("all nine station IDs and API keys must be distinct")
        stations.add(station)
        tokens.add(token)
    return players


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, default=ROOT / "main-credentials.json")
    parser.add_argument("--ws-url", default=DEFAULT_WS_URL)
    parser.add_argument("--dry-run", action="store_true", help="validate configuration without connecting")
    args = parser.parse_args()
    endpoint = urlparse(args.ws_url)
    if endpoint.scheme not in ("ws", "wss") or not endpoint.hostname or endpoint.username or endpoint.password:
        parser.error("use a ws:// or wss:// endpoint without embedded credentials")
    credentials = args.credentials_file.resolve()
    try:
        players = read_players(credentials)
    except (OSError, ValueError):
        # JSON decoder errors may include source content. Never print keys.
        print("Invalid credentials file. Supply exactly nine distinct station_id/token entries "
              "and replace all placeholders. See scripts/main-credentials.example.json.", file=sys.stderr)
        return 2
    print(f"Endpoint: {args.ws_url}")
    print("Policy: ConservativeTradingStrategy")
    print("Stations: " + ", ".join(p["station_id"] for p in players))
    if args.dry_run:
        print("Configuration valid. No connections opened.")
        return 0

    output = ROOT / "runs" / ("main-" + datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    output.mkdir(parents=True)
    print(f"Logs: {output}", flush=True)
    env = os.environ.copy()
    # A single inherited token must not override the nine file entries.
    env.pop("BAZAAR_TOKEN", None)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    processes = []
    try:
        for player in players:
            station = player["station_id"]
            command = [sys.executable, "-m", "planet_charlu.main", "--mode", "trade",
                       "--conservative-trading", "--no-open-browser", "--ws-url", args.ws_url,
                       "--credentials-file", str(credentials), "--station-id", station,
                       "--run-log", str(output / f"{station}.jsonl")]
            with (output / f"{station}.console.log").open("w") as log:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            processes.append((station, process))
            print(f"Started {station} (PID {process.pid})", flush=True)
        failed = False
        for station, process in processes:
            code = process.wait()
            failed |= code != 0
            print(f"{station} exited with code {code}; inspect its log for survival results.", flush=True)
        return 1 if failed else 0
    except KeyboardInterrupt:
        print("Stopping all nine clients.", flush=True)
        return 130
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
