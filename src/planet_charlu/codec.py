"""Binary Protobuf encoding/decoding for the Bazaar wire protocol.

Exactly one ``bazaar.v2.ClientMessage`` is sent as binary Protobuf bytes per
WebSocket message, and each incoming binary message is decoded as one
``bazaar.v2.ServerMessage``. No JSON, Base64, or length prefix is added.
"""

from __future__ import annotations

from typing import Iterable

from planet_charlu.domain.resources import Bundle, Resource, resource_to_wire
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


def build_advertise(
    *,
    run_id: str,
    request_id: str,
    selling: Iterable[Resource],
    seeking: Iterable[Resource],
    expires_tick: int,
) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.advertise.type = bazaar_pb2.ADVERTISE_TYPE_ADVERTISE
    message.advertise.protocol_version = PROTOCOL_VERSION
    message.advertise.run_id = run_id
    message.advertise.request_id = request_id
    message.advertise.body.selling.items.extend(resource_to_wire(r) for r in selling)
    message.advertise.body.seeking.items.extend(resource_to_wire(r) for r in seeking)
    message.advertise.body.expires_tick = expires_tick
    return message


def build_offer(
    *,
    run_id: str,
    request_id: str,
    recipient_id: str,
    give: Bundle,
    receive: Bundle,
    expires_tick: int,
) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.offer.type = bazaar_pb2.OFFER_COMMAND_TYPE_OFFER
    message.offer.protocol_version = PROTOCOL_VERSION
    message.offer.run_id = run_id
    message.offer.request_id = request_id
    message.offer.body.recipient_id = recipient_id
    message.offer.body.give.CopyFrom(give.to_wire())
    message.offer.body.receive.CopyFrom(receive.to_wire())
    message.offer.body.expires_tick = expires_tick
    return message


def build_accept(*, run_id: str, request_id: str, offer_id: str) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.accept.type = bazaar_pb2.ACCEPT_TYPE_ACCEPT
    message.accept.protocol_version = PROTOCOL_VERSION
    message.accept.run_id = run_id
    message.accept.request_id = request_id
    message.accept.body.offer_id = offer_id
    return message


def build_withdraw(*, run_id: str, request_id: str, object_id: str) -> bazaar_pb2.ClientMessage:
    message = bazaar_pb2.ClientMessage()
    message.withdraw.type = bazaar_pb2.WITHDRAW_TYPE_WITHDRAW
    message.withdraw.protocol_version = PROTOCOL_VERSION
    message.withdraw.run_id = run_id
    message.withdraw.request_id = request_id
    message.withdraw.body.object_id = object_id
    return message
