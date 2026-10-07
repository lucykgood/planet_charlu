"""Test nine conservative clients in 50%, 25%, and zero-surplus economies.

Uses the sibling spaceport_test_server over real localhost WebSockets.
Default trials last 300 ticks at one second per tick. --tick-ms 100 is a
development shortcut, not evidence of one-second pacing. No real keys needed.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


async def trial(args, percent, production, output):
    from websockets.asyncio.server import serve
    from spaceport_test_server.demo_server import DemoBazaar, DemoBazaarServer, Scenario
    from planet_charlu.config import ClientConfig
    from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
    from planet_charlu.session import open_session
    from planet_charlu.strategy import run_trading

    folder = output / f"surplus-{percent}"
    folder.mkdir(parents=True)
    resources = ("water", "food", "components")
    scenario = {
        "duration_ticks": args.ticks, "tick_duration_ms": args.tick_ms,
        "minimum_ready_stations": 9,
        "economy": {"upkeep": dict.fromkeys(resources, 4),
                    "production_per_tick": production, "max_health": 100,
                    "shortage_damage_per_unit": 4, "recovery_per_fully_supplied_tick": 1,
                    "max_offer_ttl_ticks": 3, "max_publication_ttl_ticks": 3,
                    "max_open_outgoing_offers": 1},
        "stations": [{"id": f"P{i+1:02d}", "specialty": resources[i % 3],
                      "inventory": dict.fromkeys(resources, 120)} for i in range(9)],
    }
    path = folder / "scenario.json"
    path.write_text(json.dumps(scenario, indent=2) + "\n")
    world = DemoBazaar(Scenario.from_file(path))

    async def client(url, number):
        async with BazaarConnection(ClientConfig(url, "local-test", f"P{number:02d}")) as connection:
            session = await open_session(connection)
            try:
                return await run_trading(session, conservative=True)
            finally:
                await session.stop_pump()

    async def progress():
        while world.phase != "FINISHED":
            await asyncio.sleep(min(30, args.ticks * args.tick_ms / 1000))
            alive = sum(not s.failed_once and s.health > 0 for s in world.stations.values())
            print(f"{percent}%: tick {world.tick}/{args.ticks}, {alive}/9 alive", flush=True)

    tasks = []
    try:
        async with serve(DemoBazaarServer(world).handler, "127.0.0.1", 0,
                         subprotocols=[REQUIRED_SUBPROTOCOL]) as server:
            port = server.sockets[0].getsockname()[1]
            tasks = [asyncio.create_task(world.run_ticks()), asyncio.create_task(progress())]
            clients = [asyncio.create_task(client(f"ws://127.0.0.1:{port}/ws", n)) for n in range(1, 10)]
            tasks += clients
            finals = await asyncio.wait_for(asyncio.gather(*clients),
                                           args.ticks * args.tick_ms / 1000 + 30)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await world.close()
    result = {
        "surplus_percent": percent, "tick_ms": args.tick_ms, "duration_ticks": args.ticks,
        "policy": "ConservativeTradingStrategy",
        "collective_success": all(f.tick == args.ticks and f.self.health > 0
                                  and not f.self.failed_once for f in finals),
        "stations": [{"station": f.self_station_id, "tick": f.tick, "health": f.self.health,
                      "failed_once": f.self.failed_once, "shortage_ticks": f.self.shortage_ticks,
                      "inventory": vars(f.self.inventory)} for f in finals],
    }
    (folder / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{percent}% surplus: {'PASS' if result['collective_success'] else 'FAIL'}; "
          f"health={[f.self.health for f in finals]}", flush=True)
    return result


async def main(args):
    output = ROOT / "runs" / ("collective-" + datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    output.mkdir(parents=True)
    print(f"Results and reusable scenarios: {output}", flush=True)
    with (output / "clients.log").open("w") as stream:
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("planet_charlu")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            results = await asyncio.gather(*(trial(args, percent, production, output)
                                            for percent, production in ((50, 18), (25, 15), (0, 12))
                                            if args.surplus is None or args.surplus == percent))
        finally:
            logger.removeHandler(handler)
    return 0 if all(r["collective_success"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-root", type=Path, default=ROOT.parent / "spaceport_test_server")
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--tick-ms", type=int, default=1000)
    parser.add_argument("--surplus", type=int, choices=(0, 25, 50))
    args = parser.parse_args()
    if args.ticks < 1 or args.tick_ms < 1:
        parser.error("ticks and tick-ms must be positive")
    sys.path.insert(0, str(args.server_root / "src"))
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(asyncio.run(main(args)))
