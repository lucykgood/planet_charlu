"""Integration tests against a real local WebSocket server.

These do not require the course validator binary: a minimal in-process
``websockets.serve`` server stands in for it, so tests can assert exactly
what headers and subprotocol the client sends and that binary Protobuf
frames survive a real socket round trip.
"""

from __future__ import annotations

import asyncio

import pytest
import websockets

from planet_charlu import codec
from planet_charlu.config import ClientConfig
from planet_charlu.connection import (
    REQUIRED_SUBPROTOCOL,
    BazaarConnection,
    SubprotocolMismatchError,
)
from planet_charlu.generated import bazaar_pb2

from fixtures import make_readiness, make_state


async def _serve(
    handler,
    *,
    subprotocols=(REQUIRED_SUBPROTOCOL,),
    process_request=None,
    select_subprotocol=None,
):
    server = await websockets.serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=list(subprotocols),
        process_request=process_request,
        select_subprotocol=select_subprotocol,
    )
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/ws"


async def test_connect_sends_bearer_token_and_negotiates_subprotocol():
    captured_authorization = None

    async def capture_headers(connection, request):
        nonlocal captured_authorization
        captured_authorization = request.headers.get("Authorization")
        return None

    async def handler(websocket):
        await websocket.close()

    server, url = await _serve(handler, process_request=capture_headers)
    try:
        config = ClientConfig(ws_url=url, token="unit-test-token", station_id="P01")
        connection = BazaarConnection(config)
        await connection.connect()
        try:
            assert captured_authorization == "Bearer unit-test-token"
        finally:
            await connection.close()
    finally:
        server.close()
        await server.wait_closed()


async def test_subprotocol_mismatch_raises_and_closes():
    async def handler(websocket):
        await websocket.close()

    # The server lists our required subprotocol as acceptable but its
    # selector declines to pick one, so the handshake succeeds (HTTP 101)
    # with no Sec-WebSocket-Protocol chosen. The client must treat that as
    # a mismatch rather than silently proceeding without the agreed codec.
    server, url = await _serve(handler, select_subprotocol=lambda conn, subprotocols: None)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        connection = BazaarConnection(config)
        with pytest.raises(SubprotocolMismatchError):
            await connection.connect()
    finally:
        server.close()
        await server.wait_closed()


async def test_send_transmits_binary_protobuf_without_wrapping():
    received = asyncio.get_event_loop().create_future()

    async def handler(websocket):
        raw = await websocket.recv()
        received.set_result(raw)
        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            await connection.send(codec.build_sync(run_id="run-1"))
            raw = await asyncio.wait_for(received, timeout=5)

        assert isinstance(raw, bytes)
        decoded = codec.decode_client_message(raw)
        assert decoded.WhichOneof("message") == "sync"
        assert decoded.sync.run_id == "run-1"
    finally:
        server.close()
        await server.wait_closed()


async def test_messages_decodes_state_then_readiness_then_stops_on_clean_close():
    async def handler(websocket):
        state_message = bazaar_pb2.ServerMessage()
        state_message.state.CopyFrom(make_state(run_id="run-1", snapshot_sequence=1))
        await websocket.send(state_message.SerializeToString())

        readiness_message = bazaar_pb2.ServerMessage()
        readiness_message.readiness.CopyFrom(make_readiness(run_id="run-1"))
        await websocket.send(readiness_message.SerializeToString())

        await websocket.close()

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            received = [message async for message in connection.messages()]

        assert [m.WhichOneof("message") for m in received] == ["state", "readiness"]
        assert received[0].state.run_id == "run-1"
        assert received[1].readiness.run_id == "run-1"
    finally:
        server.close()
        await server.wait_closed()


async def test_send_before_connect_raises_runtime_error():
    config = ClientConfig(ws_url="ws://unused/ws", token="t", station_id="P01")
    connection = BazaarConnection(config)

    with pytest.raises(RuntimeError, match="not connected"):
        await connection.send(codec.build_sync(run_id="run-1"))


async def test_messages_before_connect_raises_runtime_error():
    config = ClientConfig(ws_url="ws://unused/ws", token="t", station_id="P01")
    connection = BazaarConnection(config)

    with pytest.raises(RuntimeError, match="not connected"):
        async for _ in connection.messages():
            pass


async def test_messages_rejects_unexpected_text_frame():
    async def handler(websocket):
        await websocket.send("not protobuf, a text frame")

    server, url = await _serve(handler)
    try:
        config = ClientConfig(ws_url=url, token="t", station_id="P01")
        async with BazaarConnection(config) as connection:
            with pytest.raises(ValueError, match="text frame"):
                async for _ in connection.messages():
                    pass
    finally:
        server.close()
        await server.wait_closed()
