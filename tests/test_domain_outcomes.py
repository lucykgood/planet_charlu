from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.generated import bazaar_pb2

from fixtures import make_result


def test_from_wire_maps_success_fields_and_leaves_nulls_as_none():
    wire = make_result(request_id="req-1", ok=True, code=bazaar_pb2.RESULT_CODE_OK)

    outcome = CommandOutcome.from_wire(wire)

    assert outcome.request_id == "req-1"
    assert outcome.ok is True
    assert outcome.object_id is None
    assert outcome.transaction_id is None
    assert outcome.retry_after_tick is None


def test_from_wire_reads_present_optional_values():
    wire = make_result(
        object_id="advertisement-2",
        transaction_id="txn-1",
        retry_after_tick=5,
        code=bazaar_pb2.RESULT_CODE_RATE_LIMITED,
        ok=False,
    )

    outcome = CommandOutcome.from_wire(wire)

    assert outcome.object_id == "advertisement-2"
    assert outcome.transaction_id == "txn-1"
    assert outcome.retry_after_tick == 5
    assert outcome.ok is False


def test_code_name_reflects_the_wire_enum():
    wire = make_result(code=bazaar_pb2.RESULT_CODE_INSUFFICIENT_RESOURCES)

    assert CommandOutcome.from_wire(wire).code_name == "RESULT_CODE_INSUFFICIENT_RESOURCES"
