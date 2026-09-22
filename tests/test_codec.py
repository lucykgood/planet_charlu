import pytest
from google.protobuf.message import DecodeError

from planet_charlu import codec
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.generated import bazaar_pb2

from fixtures import make_readiness, make_state


def test_build_ready_sets_required_fields():
    message = codec.build_ready(run_id="run-1", ready=True, snapshot_sequence=1)

    assert message.WhichOneof("message") == "ready"
    assert message.ready.type == bazaar_pb2.READY_TYPE_READY
    assert message.ready.protocol_version == "2.0"
    assert message.ready.run_id == "run-1"
    assert message.ready.ready is True
    assert message.ready.snapshot_sequence == 1


def test_build_sync_sets_required_fields():
    message = codec.build_sync(run_id="run-1")

    assert message.WhichOneof("message") == "sync"
    assert message.sync.type == bazaar_pb2.SYNC_TYPE_SYNC
    assert message.sync.run_id == "run-1"


def test_client_message_round_trip_preserves_false_and_zero():
    # ready: false and snapshot_sequence: 0 are meaningful values, not
    # "unset"; proto2 required fields must serialize them rather than
    # silently dropping falsy defaults the way proto3 optional fields would.
    message = codec.build_ready(run_id="run-1", ready=False, snapshot_sequence=0)

    decoded = codec.decode_client_message(codec.encode_client_message(message))

    assert decoded.ready.ready is False
    assert decoded.ready.snapshot_sequence == 0


def test_decode_server_message_state_round_trip():
    state = make_state(run_id="run-1", snapshot_sequence=1, world_version=2)
    server_message = bazaar_pb2.ServerMessage()
    server_message.state.CopyFrom(state)

    decoded = codec.decode_server_message(server_message.SerializeToString())

    assert decoded.WhichOneof("message") == "state"
    assert decoded.state.run_id == "run-1"
    assert decoded.state.snapshot_sequence == 1
    assert decoded.state.phase == bazaar_pb2.PHASE_RUNNING


def test_decode_server_message_readiness_round_trip():
    readiness = make_readiness(run_id="run-1", ready=True, snapshot_sequence=1)
    server_message = bazaar_pb2.ServerMessage()
    server_message.readiness.CopyFrom(readiness)

    decoded = codec.decode_server_message(server_message.SerializeToString())

    assert decoded.WhichOneof("message") == "readiness"
    assert decoded.readiness.ready is True


def test_decode_server_message_rejects_malformed_bytes():
    with pytest.raises(DecodeError):
        codec.decode_server_message(b"\xff\xff\xff\xff\xff\xff\xff\xff\xff")


def test_build_advertise_sets_required_fields():
    message = codec.build_advertise(
        run_id="run-1",
        request_id="req-1",
        selling=[Resource.WATER],
        seeking=[Resource.FOOD],
        expires_tick=10,
    )

    assert message.WhichOneof("message") == "advertise"
    assert message.advertise.type == bazaar_pb2.ADVERTISE_TYPE_ADVERTISE
    assert message.advertise.protocol_version == "2.0"
    assert message.advertise.run_id == "run-1"
    assert message.advertise.request_id == "req-1"
    assert list(message.advertise.body.selling.items) == [bazaar_pb2.RESOURCE_WATER]
    assert list(message.advertise.body.seeking.items) == [bazaar_pb2.RESOURCE_FOOD]
    assert message.advertise.body.expires_tick == 10


def test_build_offer_sets_required_fields():
    message = codec.build_offer(
        run_id="run-1",
        request_id="req-1",
        recipient_id="P02",
        give=Bundle(water=2),
        receive=Bundle(food=1),
        expires_tick=10,
    )

    assert message.WhichOneof("message") == "offer"
    assert message.offer.type == bazaar_pb2.OFFER_COMMAND_TYPE_OFFER
    assert message.offer.run_id == "run-1"
    assert message.offer.request_id == "req-1"
    assert message.offer.body.recipient_id == "P02"
    assert message.offer.body.give.water == 2
    assert message.offer.body.receive.food == 1
    assert message.offer.body.expires_tick == 10


def test_build_accept_sets_required_fields():
    message = codec.build_accept(run_id="run-1", request_id="req-1", offer_id="offer-1")

    assert message.WhichOneof("message") == "accept"
    assert message.accept.type == bazaar_pb2.ACCEPT_TYPE_ACCEPT
    assert message.accept.run_id == "run-1"
    assert message.accept.request_id == "req-1"
    assert message.accept.body.offer_id == "offer-1"


def test_build_withdraw_sets_required_fields():
    message = codec.build_withdraw(run_id="run-1", request_id="req-1", object_id="ad-1")

    assert message.WhichOneof("message") == "withdraw"
    assert message.withdraw.type == bazaar_pb2.WITHDRAW_TYPE_WITHDRAW
    assert message.withdraw.run_id == "run-1"
    assert message.withdraw.request_id == "req-1"
    assert message.withdraw.body.object_id == "ad-1"


def test_command_messages_round_trip():
    advertise = codec.build_advertise(
        run_id="run-1",
        request_id="req-1",
        selling=[Resource.WATER],
        seeking=[Resource.FOOD],
        expires_tick=10,
    )

    decoded = codec.decode_client_message(codec.encode_client_message(advertise))

    assert decoded.WhichOneof("message") == "advertise"
    assert list(decoded.advertise.body.selling.items) == [bazaar_pb2.RESOURCE_WATER]
