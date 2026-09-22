"""Tests for the required connection sequence against a local fake server."""

from __future__ import annotations

import asyncio

import pytest
import websockets

from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.generated import bazaar_pb2
from planet_charlu.session import HandshakeError, perform_readiness_handshake, run

from fixtures import make_readiness, make_result, make_state


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
