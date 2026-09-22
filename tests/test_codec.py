import pytest
from google.protobuf.message import DecodeError

from planet_charlu import codec
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
