#!/usr/bin/env python3
"""Exercise the trading loop, the per-tick dashboard, and --run-log against a
scripted local WebSocket server -- no live match, no bundled validator
binary, and no network access required.

This answers "how do we know the logging actually works" when there's
nothing real to connect to yet: it's the same trick
tests/test_strategy.py::test_nine_planet_websocket_simulation uses to test
run_trading() itself -- a tiny in-process server that speaks just enough of
the protocol to hand our station a few ticks, one gift, and one reciprocated
trade with a single partner, then ends the run.

Run from the repository root:

    PYTHONPATH=src python scripts/demo_local_run.py
    PYTHONPATH=src python scripts/demo_local_run.py --run-log /tmp/demo.jsonl
    python scripts/generate_run_summary.py /tmp/demo.jsonl   # then view the HTML
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import websockets

from fixtures import (
    make_advertisement,
    make_bundle,
    make_offer,
    make_readiness,
    make_result,
    make_self_observation,
    make_state,
    make_transaction,
)
from planet_charlu import codec
from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.logging_utils import configure_logging
from planet_charlu.session import open_session
from planet_charlu.strategy import run_trading
from planet_charlu.structured_log import RunLog


async def _scripted_server(socket) -> None:
    """Plays one cooperative partner, P02, across a handful of ticks."""
    inventory = [30, 2, 5]  # matches the strategy's own critical-food scenario
    offers = []
    transactions = []
    ads = [make_advertisement(station_id="P02", selling=[pb.RESOURCE_FOOD],
                               seeking=[pb.RESOURCE_WATER], expires_tick=100)]
    sequence = 0
    tick = 0
    phase = pb.PHASE_READY

    async def send_state():
        nonlocal sequence
        sequence += 1
        state = make_state(snapshot_sequence=sequence, world_version=sequence, tick=tick,
                            phase=phase, offers=offers, advertisements=ads, transactions=transactions,
                            self_observation=make_self_observation(inventory=make_bundle(*inventory)))
        state.rules.max_request_records_per_station = 100
        message = pb.ServerMessage()
        message.state.CopyFrom(state)
        await socket.send(message.SerializeToString())

    await send_state()
    ready = codec.decode_client_message(await socket.recv())
    assert ready.WhichOneof("message") == "ready"
    response = pb.ServerMessage()
    response.readiness.CopyFrom(make_readiness())
    await socket.send(response.SerializeToString())
    phase = pb.PHASE_RUNNING
    await send_state()

    async for raw in socket:
        message = codec.decode_client_message(raw)
        kind = message.WhichOneof("message")
        if kind == "sync":
            tick += 1
            if tick >= 6:
                phase = pb.PHASE_FINISHED
            await send_state()
            if phase == pb.PHASE_FINISHED:
                return
            continue
        command = getattr(message, kind)
        object_id = None
        if kind == "advertise":
            # Without recording our own ad, `world.advertisements` would never
            # reflect it, so the strategy would see `ad is None` forever and
            # just keep re-publishing instead of moving on to seek a trade.
            object_id = command.request_id
            ads[:] = [a for a in ads if a.station_id != "P01"]
            ads.append(make_advertisement(advertisement_id=object_id, station_id="P01",
                                           selling=list(command.body.selling.items),
                                           seeking=list(command.body.seeking.items),
                                           created_tick=tick, expires_tick=command.body.expires_tick))
        elif kind == "offer":
            body = command.body
            # P02 immediately reciprocates: settle our offer as an accepted trade,
            # so the dashboard's "recent trades" and the JSON log's
            # transaction_settled event both have something real to show.
            object_id = command.request_id
            offers.append(make_offer(offer_id=object_id, proposer_id="P01", recipient_id="P02",
                                      give=body.give, receive=body.receive, created_tick=tick,
                                      expires_tick=body.expires_tick, status=pb.OFFER_STATUS_ACCEPTED,
                                      transaction_id=object_id))
            transactions.append(make_transaction(transaction_id=object_id, offer_id=object_id,
                                                  proposer_id="P01", recipient_id="P02",
                                                  give=body.give, receive=body.receive, settled_tick=tick))
            inventory[0] -= body.give.water
            inventory[1] += body.receive.food
        response = pb.ServerMessage()
        response.result.CopyFrom(make_result(request_id=command.request_id, object_id=object_id))
        await socket.send(response.SerializeToString())


async def _run(run_log_path: str | None) -> None:
    async with websockets.serve(_scripted_server, "127.0.0.1", 0, subprotocols=[REQUIRED_SUBPROTOCOL]) as server:
        port = server.sockets[0].getsockname()[1]
        async with BazaarConnection(ClientConfig(f"ws://127.0.0.1:{port}", "demo-token", "P01")) as connection:
            session = await open_session(connection)
            run_log = RunLog.open(run_log_path) if run_log_path else None
            try:
                final = await asyncio.wait_for(run_trading(session, run_log=run_log), timeout=10)
            finally:
                await session.stop_pump()
                if run_log:
                    run_log.close()
    print(f"\ndemo run finished: final inventory={final.self.inventory}, "
          f"{len(final.transactions)} transaction(s)")
    if run_log_path:
        print(f"structured log written to {run_log_path}")
        print(f"generate a summary with: python scripts/generate_run_summary.py {run_log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-log", default=None, help="Also write a structured JSON Lines log here")
    args = parser.parse_args()

    configure_logging()
    asyncio.run(_run(args.run_log))


if __name__ == "__main__":
    main()
