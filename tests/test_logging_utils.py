from planet_charlu.generated import bazaar_pb2
from planet_charlu.logging_utils import describe_server_message

from fixtures import make_protocol_error, make_readiness, make_result, make_state


def test_describe_state_includes_phase_and_health():
    message = bazaar_pb2.ServerMessage()
    message.state.CopyFrom(make_state(phase=bazaar_pb2.PHASE_RUNNING))

    summary = describe_server_message(message)

    assert "PHASE_RUNNING" in summary
    assert "health=100" in summary
    assert "self=P01" in summary


def test_describe_state_includes_specialty_and_inventory():
    message = bazaar_pb2.ServerMessage()
    message.state.CopyFrom(make_state())

    summary = describe_server_message(message)

    assert "specialty=RESOURCE_WATER" in summary
    assert "inventory=(water=30,food=30,components=30)" in summary


def test_describe_result_includes_ok_and_code():
    message = bazaar_pb2.ServerMessage()
    message.result.CopyFrom(make_result(ok=True, code=bazaar_pb2.RESULT_CODE_OK))

    summary = describe_server_message(message)

    assert "ok=True" in summary
    assert "RESULT_CODE_OK" in summary


def test_describe_readiness_includes_ready_flag():
    message = bazaar_pb2.ServerMessage()
    message.readiness.CopyFrom(make_readiness(ready=True))

    summary = describe_server_message(message)

    assert "ready=True" in summary


def test_describe_protocol_error_includes_code_and_close_session():
    message = bazaar_pb2.ServerMessage()
    message.protocol_error.CopyFrom(
        make_protocol_error(
            code=bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, close_session=False
        )
    )

    summary = describe_server_message(message)

    assert "CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED" in summary
    assert "close_session=False" in summary


def test_describe_unset_message_does_not_raise():
    message = bazaar_pb2.ServerMessage()

    summary = describe_server_message(message)

    assert "unset" in summary
