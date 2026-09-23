"""Tests for the required connection sequence against a local fake server."""

from __future__ import annotations

import asyncio

import pytest
import websockets

from planet_charlu import codec
from planet_charlu.commands import ProtocolErrorReceived
from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2
from planet_charlu.session import (
    HandshakeError,
    NotRunningError,
    open_session,
    perform_readiness_handshake,
    run,
)

from fixtures import (
    make_advertisement,
    make_bundle,
    make_protocol_error,
    make_readiness,
    make_result,
    make_self_observation,
    make_state,
)


async def _serve(handler):
    server = await websockets.serve(
        handler, "127.0.0.1", 0, subprotocols=[REQUIRED_SUBPROTOCOL]
    )
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/ws"


async def test_handshake_sends_ready_and_returns_initial_state():
    sent_ready = {}

    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        raw = await websocket.recv()
        client_message = bazaar_pb2.ClientMessage()
        client_message.ParseFromString(raw)
        sent_ready["message"] = client_message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            state = await perform_readiness_handshake(connection, messages)

        assert state.run_id == "run-1"
        assert sent_ready["message"].WhichOneof("message") == "ready"
        assert sent_ready["message"].ready.run_id == "run-1"
        assert sent_ready["message"].ready.ready is True
        assert sent_ready["message"].ready.snapshot_sequence == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_raises_if_first_message_is_not_state():
    async def handler(websocket):
        message = bazaar_pb2.ServerMessage()
        message.result.CopyFrom(make_result())
        await websocket.send(message.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            with pytest.raises(HandshakeError, match="expected initial state"):
                await perform_readiness_handshake(connection, messages)
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_raises_if_second_message_is_not_readiness():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        another_state = bazaar_pb2.ServerMessage()
        another_state.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=2))
        await websocket.send(another_state.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            with pytest.raises(HandshakeError, match="expected readiness confirmation"):
                await perform_readiness_handshake(connection, messages)
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_raises_if_readiness_does_not_match_run_id():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="a-different-run", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            with pytest.raises(HandshakeError, match="did not match"):
                await perform_readiness_handshake(connection, messages)
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_raises_if_readiness_snapshot_sequence_mismatches():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=2)
        )
        await websocket.send(readiness_message.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            with pytest.raises(HandshakeError, match="did not match"):
                await perform_readiness_handshake(connection, messages)
    finally:
        server.close()
        await server.wait_closed()


async def test_handshake_step1_scenario_matches_validator_spec():
    """Pins validator/README.md's exact Step 1 "Check" bullets: P01,
    inventory (30,30,30), specialty water, and P02 selling food/seeking
    water. ``make_state``'s defaults already model this scenario; this test
    exists so a future change to those defaults (or to WorldView decoding)
    that breaks the documented scenario fails loudly here instead of only
    being caught by eye against a live validator.
    """
    p02_ad = make_advertisement(station_id="P02")

    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(
            make_state(run_id="run-1", snapshot_sequence=1, advertisements=[p02_ad])
        )
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            messages = connection.messages()
            state = await perform_readiness_handshake(connection, messages)
    finally:
        server.close()
        await server.wait_closed()

    assert state.world_version == 2
    assert state.snapshot_sequence == 1

    world = WorldView.from_state(state)
    assert world.self_station_id == "P01"
    assert world.self.inventory.water == 30
    assert world.self.inventory.food == 30
    assert world.self.inventory.components == 30
    assert world.self.specialty is Resource.WATER
    assert any(
        ad.posted_by("P02") and Resource.FOOD in ad.selling and Resource.WATER in ad.seeking
        for ad in world.advertisements
    )


async def test_run_completes_handshake_then_logs_until_server_closes():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())

        # One post-handshake message to prove the continuous loop keeps
        # decoding after readiness, not just during the handshake.
        result_message = bazaar_pb2.ServerMessage()
        result_message.result.CopyFrom(make_result())
        await websocket.send(result_message.SerializeToString())

        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        await asyncio.wait_for(run(config), timeout=5)
    finally:
        server.close()
        await server.wait_closed()


async def test_open_session_world_updates_as_new_state_arrives():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())

        next_state = bazaar_pb2.ServerMessage()
        next_state.state.CopyFrom(
            make_state(
                run_id="run-1",
                snapshot_sequence=2,
                self_observation=make_self_observation(inventory=make_bundle(20, 20, 20)),
            )
        )
        await websocket.send(next_state.SerializeToString())
        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            assert session.world.self.inventory == Bundle(30, 30, 30)

            for _ in range(50):
                if session.world.self.inventory == Bundle(20, 20, 20):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("ClientSession.world was not updated from the follow-up state")

            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()


async def test_client_session_send_raises_when_not_running():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(
            make_state(run_id="run-1", snapshot_sequence=1, phase=bazaar_pb2.PHASE_PAUSED)
        )
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())
        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            with pytest.raises(NotRunningError):
                await session.send(
                    request_id="req-1",
                    message=codec.build_withdraw(
                        run_id="run-1", request_id="req-1", object_id="ad-1"
                    ),
                )
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()


async def test_client_session_send_returns_correlated_result():
    sent_command = {}

    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())

        raw = await websocket.recv()  # the command sent by ClientSession.send
        client_message = bazaar_pb2.ClientMessage()
        client_message.ParseFromString(raw)
        sent_command["message"] = client_message

        result_message = bazaar_pb2.ServerMessage()
        result_message.result.CopyFrom(make_result(request_id="req-1", ok=True))
        await websocket.send(result_message.SerializeToString())
        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            outcome = await asyncio.wait_for(
                session.send(
                    request_id="req-1",
                    message=codec.build_withdraw(
                        run_id="run-1", request_id="req-1", object_id="ad-1"
                    ),
                ),
                timeout=2,
            )
            assert outcome.request_id == "req-1"
            assert outcome.ok is True
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()

    assert sent_command["message"].WhichOneof("message") == "withdraw"
    assert sent_command["message"].withdraw.request_id == "req-1"


async def test_client_session_send_raises_protocol_error_received():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        await websocket.recv()  # the client's ready message

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(
            make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
        )
        await websocket.send(readiness_message.SerializeToString())

        await websocket.recv()  # the command sent by ClientSession.send

        error_message = bazaar_pb2.ServerMessage()
        error_message.protocol_error.CopyFrom(
            make_protocol_error(
                code=bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
                close_session=False,
                request_id="req-1",
            )
        )
        await websocket.send(error_message.SerializeToString())
        await asyncio.sleep(0.05)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            session = await open_session(connection)
            with pytest.raises(ProtocolErrorReceived) as exc_info:
                await asyncio.wait_for(
                    session.send(
                        request_id="req-1",
                        message=codec.build_withdraw(
                            run_id="run-1", request_id="req-1", object_id="ad-1"
                        ),
                    ),
                    timeout=2,
                )
            assert exc_info.value.code == bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
            assert exc_info.value.close_session is False
            await session.stop_pump()
    finally:
        server.close()
        await server.wait_closed()
