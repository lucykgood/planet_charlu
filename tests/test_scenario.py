"""Tests for the ten-step scenario driver against a local fake server.

The fake server in ``_run_full_scenario_server`` plays exactly the sequence
documented in validator/README.md's "Complete the exchange" section, so a
regression in ``scenario.run_sample_scenario``'s request order, field usage,
or state tracking fails here instead of only showing up against the real
validator binary.
"""

from __future__ import annotations

import asyncio

import pytest
import websockets

from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.domain.resources import Bundle
from planet_charlu.generated import bazaar_pb2
from planet_charlu.scenario import ScenarioError, run_sample_scenario
from planet_charlu.session import open_session

from fixtures import (
    make_advertisement,
    make_bundle,
    make_offer,
    make_protocol_error,
    make_readiness,
    make_result,
    make_self_observation,
    make_state,
    make_transaction,
)


async def _serve(handler):
    server = await websockets.serve(
        handler, "127.0.0.1", 0, subprotocols=[REQUIRED_SUBPROTOCOL]
    )
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/ws"


async def _send_state(websocket, state):
    message = bazaar_pb2.ServerMessage()
    message.state.CopyFrom(state)
    await websocket.send(message.SerializeToString())


async def _send_result(websocket, **kwargs):
    message = bazaar_pb2.ServerMessage()
    message.result.CopyFrom(make_result(**kwargs))
    await websocket.send(message.SerializeToString())


async def _recv_client_message(websocket):
    raw = await websocket.recv()
    message = bazaar_pb2.ClientMessage()
    message.ParseFromString(raw)
    return message


async def _handshake(websocket, *, run_id: str, initial_state) -> None:
    await _send_state(websocket, initial_state)
    await websocket.recv()  # the client's ready message
    message = bazaar_pb2.ServerMessage()
    message.readiness.CopyFrom(make_readiness(run_id=run_id, ready=True, snapshot_sequence=1))
    await websocket.send(message.SerializeToString())


def _full_scenario_handler(run_id: str = "run-1"):
    async def handler(websocket):
        await _handshake(
            websocket,
            run_id=run_id,
            initial_state=make_state(run_id=run_id, snapshot_sequence=1, world_version=2),
        )

        # Step 2: advertise water for food.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "advertise"
        assert client_message.advertise.request_id == "student-advertise-1"
        await _send_result(websocket, run_id=run_id, request_id="student-advertise-1", ok=True)
        await _send_state(
            websocket, make_state(run_id=run_id, snapshot_sequence=2, world_version=3)
        )

        # Step 3: replace advertisement, seeking components.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "advertise"
        assert client_message.advertise.request_id == "student-advertise-seeking-1"
        await _send_result(
            websocket,
            run_id=run_id,
            request_id="student-advertise-seeking-1",
            ok=True,
            object_id="ad-1",
        )
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=3,
                world_version=4,
                advertisements=[make_advertisement(advertisement_id="ad-1", station_id="P01")],
            ),
        )

        # Step 4: offer two water for one food.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "offer"
        assert client_message.offer.request_id == "student-offer-1"
        await _send_result(
            websocket, run_id=run_id, request_id="student-offer-1", ok=True, object_id="offer-1"
        )
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=4,
                world_version=5,
                offers=[make_offer(offer_id="offer-1")],
            ),
        )

        # Step 5: P02 accepts our offer, no command from us.
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=5,
                world_version=6,
                self_observation=make_self_observation(inventory=make_bundle(28, 31, 30)),
                offers=[make_offer(offer_id="offer-1", status=bazaar_pb2.OFFER_STATUS_ACCEPTED)],
                transactions=[make_transaction(transaction_id="txn-1", offer_id="offer-1")],
            ),
        )

        # Step 6: P02 offers a gift, no command from us.
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=6,
                world_version=7,
                self_observation=make_self_observation(inventory=make_bundle(28, 31, 30)),
                offers=[
                    make_offer(offer_id="offer-1", status=bazaar_pb2.OFFER_STATUS_ACCEPTED),
                    make_offer(
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                    ),
                ],
                transactions=[make_transaction(transaction_id="txn-1", offer_id="offer-1")],
            ),
        )

        # Step 7: accept the gift.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "accept"
        assert client_message.accept.request_id == "student-accept-1"
        assert client_message.accept.body.offer_id == "offer-2"
        await _send_result(
            websocket,
            run_id=run_id,
            request_id="student-accept-1",
            ok=True,
            transaction_id="txn-2",
        )
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=7,
                world_version=8,
                self_observation=make_self_observation(inventory=make_bundle(28, 31, 31)),
                offers=[
                    make_offer(offer_id="offer-1", status=bazaar_pb2.OFFER_STATUS_ACCEPTED),
                    make_offer(
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                        status=bazaar_pb2.OFFER_STATUS_ACCEPTED,
                    ),
                ],
                transactions=[
                    make_transaction(transaction_id="txn-1", offer_id="offer-1"),
                    make_transaction(
                        transaction_id="txn-2",
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                    ),
                ],
            ),
        )

        # Step 8: withdraw the advertisement.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "withdraw"
        assert client_message.withdraw.request_id == "student-withdraw-1"
        assert client_message.withdraw.body.object_id == "ad-1"
        await _send_result(websocket, run_id=run_id, request_id="student-withdraw-1", ok=True)
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=8,
                world_version=9,
                self_observation=make_self_observation(inventory=make_bundle(28, 31, 31)),
                transactions=[
                    make_transaction(transaction_id="txn-1", offer_id="offer-1"),
                    make_transaction(
                        transaction_id="txn-2",
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                    ),
                ],
                request_results=[
                    make_result(run_id=run_id, request_id=f"stored-{i}") for i in range(5)
                ],
            ),
        )

        # Step 9: intentional request-limit error, no result and no state.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "advertise"
        assert client_message.advertise.request_id == "student-advertise-2"
        error_message = bazaar_pb2.ServerMessage()
        error_message.protocol_error.CopyFrom(
            make_protocol_error(
                code=bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
                close_session=False,
                request_id="student-advertise-2",
            )
        )
        await websocket.send(error_message.SerializeToString())

        # Step 10: sync, answered with only a state.
        client_message = await _recv_client_message(websocket)
        assert client_message.WhichOneof("message") == "sync"
        await _send_state(
            websocket,
            make_state(
                run_id=run_id,
                snapshot_sequence=9,
                world_version=9,
                self_observation=make_self_observation(inventory=make_bundle(28, 31, 31)),
                transactions=[
                    make_transaction(transaction_id="txn-1", offer_id="offer-1"),
                    make_transaction(
                        transaction_id="txn-2",
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                    ),
                ],
                request_results=[
                    make_result(run_id=run_id, request_id=f"stored-{i}") for i in range(5)
                ],
            ),
        )

        await asyncio.sleep(0.05)
        await websocket.close()

    return handler


async def test_run_sample_scenario_matches_validator_spec():
    server, url = await _serve(_full_scenario_handler())
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            world = await asyncio.wait_for(run_sample_scenario(session), timeout=2)
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()

    assert world.world_version == 9
    assert world.snapshot_sequence == 9
    assert world.self.inventory == Bundle(28, 31, 31)
    assert len(world.transactions) == 2
    assert len(world.request_results) == 5


async def test_run_sample_scenario_raises_when_step2_result_not_ok():
    async def handler(websocket):
        await _handshake(
            websocket, run_id="run-1", initial_state=make_state(run_id="run-1", snapshot_sequence=1)
        )
        await websocket.recv()  # the advertise from step 2
        await _send_result(
            websocket,
            run_id="run-1",
            request_id="student-advertise-1",
            ok=False,
            code=bazaar_pb2.RESULT_CODE_INVALID_ARGUMENT,
        )
        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            with pytest.raises(ScenarioError, match="step 2"):
                await asyncio.wait_for(run_sample_scenario(session), timeout=2)
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()


async def test_run_sample_scenario_raises_when_step9_succeeds_instead_of_erroring():
    async def handler(websocket):
        await _handshake(
            websocket, run_id="run-1", initial_state=make_state(run_id="run-1", snapshot_sequence=1)
        )

        await websocket.recv()  # step 2 advertise
        await _send_result(websocket, run_id="run-1", request_id="student-advertise-1", ok=True)
        await _send_state(
            websocket, make_state(run_id="run-1", snapshot_sequence=2, world_version=3)
        )

        await websocket.recv()  # step 3 advertise
        await _send_result(
            websocket,
            run_id="run-1",
            request_id="student-advertise-seeking-1",
            ok=True,
            object_id="ad-1",
        )
        await _send_state(
            websocket, make_state(run_id="run-1", snapshot_sequence=3, world_version=4)
        )

        await websocket.recv()  # step 4 offer
        await _send_result(
            websocket, run_id="run-1", request_id="student-offer-1", ok=True, object_id="offer-1"
        )
        await _send_state(
            websocket,
            make_state(
                run_id="run-1",
                snapshot_sequence=4,
                world_version=5,
                offers=[make_offer(offer_id="offer-1")],
            ),
        )
        await _send_state(
            websocket,
            make_state(
                run_id="run-1",
                snapshot_sequence=5,
                world_version=6,
                offers=[make_offer(offer_id="offer-1", status=bazaar_pb2.OFFER_STATUS_ACCEPTED)],
            ),
        )
        await _send_state(
            websocket,
            make_state(
                run_id="run-1",
                snapshot_sequence=6,
                world_version=7,
                offers=[
                    make_offer(offer_id="offer-1", status=bazaar_pb2.OFFER_STATUS_ACCEPTED),
                    make_offer(
                        offer_id="offer-2",
                        proposer_id="P02",
                        recipient_id="P01",
                        give=make_bundle(0, 0, 1),
                        receive=make_bundle(0, 0, 0),
                    ),
                ],
            ),
        )

        await websocket.recv()  # step 7 accept
        await _send_result(websocket, run_id="run-1", request_id="student-accept-1", ok=True)
        await _send_state(
            websocket, make_state(run_id="run-1", snapshot_sequence=7, world_version=8)
        )

        await websocket.recv()  # step 8 withdraw
        await _send_result(websocket, run_id="run-1", request_id="student-withdraw-1", ok=True)
        await _send_state(
            websocket, make_state(run_id="run-1", snapshot_sequence=8, world_version=9)
        )

        await websocket.recv()  # step 9 advertise -- answered as a success, not an error
        await _send_result(websocket, run_id="run-1", request_id="student-advertise-2", ok=True)
        await _send_state(
            websocket, make_state(run_id="run-1", snapshot_sequence=9, world_version=10)
        )

        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            with pytest.raises(ScenarioError, match="step 9"):
                await asyncio.wait_for(run_sample_scenario(session), timeout=2)
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()
