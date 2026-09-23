import asyncio

import pytest

from planet_charlu.commands import (
    DuplicateRequestIdError,
    PendingRequests,
    ProtocolErrorReceived,
    generate_request_id,
    send_command,
)
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.generated import bazaar_pb2

from fixtures import make_result


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[bazaar_pb2.ClientMessage] = []

    async def send(self, message: bazaar_pb2.ClientMessage) -> None:
        self.sent.append(message)


def test_generate_request_id_returns_unique_values():
    first = generate_request_id()
    second = generate_request_id()

    assert first != second
    assert isinstance(first, str) and first


async def test_register_returns_future_resolved_by_resolve():
    pending = PendingRequests()
    future = pending.register("req-1")

    outcome = CommandOutcome.from_wire(make_result(request_id="req-1", ok=True))
    pending.resolve(outcome)

    assert await asyncio.wait_for(future, timeout=1) is outcome


async def test_register_raises_on_duplicate_request_id():
    pending = PendingRequests()
    pending.register("req-1")

    with pytest.raises(DuplicateRequestIdError):
        pending.register("req-1")


def test_resolve_ignores_unknown_request_id():
    pending = PendingRequests()

    # Should not raise even though nothing registered "req-unknown".
    pending.resolve(CommandOutcome.from_wire(make_result(request_id="req-unknown")))


async def test_reject_sets_exception_on_pending_future():
    pending = PendingRequests()
    future = pending.register("req-1")

    error = ProtocolErrorReceived(
        code=bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, close_session=False
    )
    pending.reject("req-1", error)

    with pytest.raises(ProtocolErrorReceived):
        await asyncio.wait_for(future, timeout=1)


def test_reject_ignores_unknown_request_id():
    pending = PendingRequests()

    # Should not raise even though nothing registered "req-unknown".
    pending.reject(
        "req-unknown",
        ProtocolErrorReceived(code=bazaar_pb2.CONTROL_CODE_BAD_MESSAGE, close_session=True),
    )


async def test_resolve_after_future_already_cancelled_does_not_raise():
    pending = PendingRequests()
    future = pending.register("req-1")
    future.cancel()

    pending.resolve(CommandOutcome.from_wire(make_result(request_id="req-1")))


async def test_send_command_sends_message_and_awaits_matching_result():
    connection = _FakeConnection()
    pending = PendingRequests()
    message = bazaar_pb2.ClientMessage()
    message.sync.type = bazaar_pb2.SYNC_TYPE_SYNC
    message.sync.protocol_version = "2.0"
    message.sync.run_id = "run-1"

    async def resolve_soon():
        await asyncio.sleep(0)
        outcome = CommandOutcome.from_wire(make_result(request_id="req-1", ok=True))
        pending.resolve(outcome)

    resolver = asyncio.create_task(resolve_soon())
    outcome = await send_command(connection, pending, request_id="req-1", message=message)
    await resolver

    assert connection.sent == [message]
    assert outcome.request_id == "req-1"
    assert outcome.ok is True


async def test_send_command_raises_on_duplicate_request_id_before_sending():
    connection = _FakeConnection()
    pending = PendingRequests()
    pending.register("req-1")
    message = bazaar_pb2.ClientMessage()
    message.sync.type = bazaar_pb2.SYNC_TYPE_SYNC
    message.sync.protocol_version = "2.0"
    message.sync.run_id = "run-1"

    with pytest.raises(DuplicateRequestIdError):
        await send_command(connection, pending, request_id="req-1", message=message)

    assert connection.sent == []
