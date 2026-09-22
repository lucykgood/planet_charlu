"""Binary Protobuf encoding/decoding for the Bazaar wire protocol.

Exactly one ``bazaar.v2.ClientMessage`` is sent as binary Protobuf bytes per
WebSocket message, and each incoming binary message is decoded as one
``bazaar.v2.ServerMessage``. No JSON, Base64, or length prefix is added.
"""

from __future__ import annotations

from planet_charlu.generated import bazaar_pb2

PROTOCOL_VERSION = "2.0"


def encode_client_message(message: bazaar_pb2.ClientMessage) -> bytes:
    return message.SerializeToString()


def decode_server_message(data: bytes) -> bazaar_pb2.ServerMessage:
    message = bazaar_pb2.ServerMessage()
    message.ParseFromString(data)
    return message


def decode_client_message(data: bytes) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.ParseFromString(data)
    return message


def build_ready(
    *, run_id: str, ready: bool, snapshot_sequence: int
) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.ready.type = bazaar_pb2.READY_TYPE_READY
    message.ready.protocol_version = PROTOCOL_VERSION
    message.ready.run_id = run_id
    message.ready.ready = ready
    message.ready.snapshot_sequence = snapshot_sequence
    return message


def build_sync(*, run_id: str) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.sync.type = bazaar_pb2.SYNC_TYPE_SYNC
    message.sync.protocol_version = PROTOCOL_VERSION
    message.sync.run_id = run_id
    return message
