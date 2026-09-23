"""Scenario orchestration: the required connection sequence and receive loop.

Steps 1-4 of the required connection sequence (connect, read initial state,
send ready, wait for readiness) live here. Sending trading commands once the
run is confirmed RUNNING is out of scope for this module; it only proves the
handshake and keeps listening.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterator, Callable, Optional

from planet_charlu import codec
from planet_charlu.commands import PendingRequests, ProtocolErrorReceived, send_command
from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2
from planet_charlu.logging_utils import describe_server_message

logger = logging.getLogger(__name__)


class HandshakeError(RuntimeError):
    """Raised when the initial state/readiness exchange does not behave as required."""


class NotRunningError(RuntimeError):
    """Raised when a trading command is attempted while the run is not RUNNING."""


async def perform_readiness_handshake(
    connection: BazaarConnection,
    messages: AsyncIterator[bazaar_pb2.ServerMessage],
) -> bazaar_pb2.State:
    """Read the initial state, declare readiness, and wait for confirmation.

    Returns the initial ``State`` so callers can read ``run_id``, phase, and
    identity without re-parsing it.
    """
    first = await anext(messages)
    if first.WhichOneof("message") != "state":
        raise HandshakeError(
            f"expected initial state, got {first.WhichOneof('message')!r}"
        )
    state = first.state
    logger.info("initial %s", describe_server_message(first))

    ready_message = codec.build_ready(
        run_id=state.run_id,
        ready=True,
        snapshot_sequence=state.snapshot_sequence,
    )
    await connection.send(ready_message)

    confirmation = await anext(messages)
    if confirmation.WhichOneof("message") != "readiness":
        raise HandshakeError(
            f"expected readiness confirmation, got {confirmation.WhichOneof('message')!r}"
        )
    readiness = confirmation.readiness
    logger.info("received %s", describe_server_message(confirmation))

    if (
        not readiness.ready
        or readiness.run_id != state.run_id
        or readiness.snapshot_sequence != state.snapshot_sequence
    ):
        raise HandshakeError(f"readiness confirmation did not match request: {readiness}")

    return state


class ClientSession:
    """A connected, handshaken session: tracks the current ``WorldView`` and
    correlates outgoing commands with their results as the receive loop runs.

    Callers use ``open_session()`` to construct one; the pump loop it starts
    is what keeps ``world`` fresh and resolves ``send()`` calls, so a
    ``ClientSession`` is only useful while its pump task is running.
    """

    def __init__(
        self,
        connection: BazaarConnection,
        messages: AsyncIterator[bazaar_pb2.ServerMessage],
        world: WorldView,
    ) -> None:
        self._connection = connection
        self._messages = messages
        self._pending = PendingRequests()
        self.world = world
        self._pump_task: Optional["asyncio.Task[None]"] = None
        self._world_updated = asyncio.Event()

    def start_pump(self) -> None:
        self._pump_task = asyncio.create_task(self._pump())

    async def stop_pump(self) -> None:
        if self._pump_task is None:
            return
        self._pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump_task
        self._pump_task = None

    async def _pump(self) -> None:
        async for message in self._messages:
            logger.info("received %s", describe_server_message(message))
            kind = message.WhichOneof("message")
            if kind == "state":
                self.world = WorldView.from_state(message.state)
                self._world_updated.set()
            elif kind == "result":
                self._pending.resolve(CommandOutcome.from_wire(message.result))
            elif kind == "protocol_error":
                error = message.protocol_error
                if not error.request_id.null:
                    self._pending.reject(
                        error.request_id.value,
                        ProtocolErrorReceived(code=error.code, close_session=error.close_session),
                    )

    async def send(self, *, request_id: str, message: bazaar_pb2.ClientMessage) -> CommandOutcome:
        """Send a trading command and await its correlated result.

        Refuses to send unless the latest ``WorldView`` reports the run is
        RUNNING, per the protocol's phase-gating requirement. Raises
        ``ProtocolErrorReceived`` instead of returning if the server answers
        with a ``protocol_error`` for this ``request_id`` rather than a
        ``result`` -- e.g. the required intentional-error validation step.
        """
        if not self.world.is_running():
            raise NotRunningError(f"cannot send while phase={self.world.phase!r}")
        return await send_command(
            self._connection, self._pending, request_id=request_id, message=message
        )

    async def wait_for(
        self, predicate: Callable[[WorldView], bool], *, timeout: float = 5.0
    ) -> WorldView:
        """Block until the pump applies a ``WorldView`` satisfying ``predicate``.

        Event-driven rather than polling: the pump signals ``_world_updated``
        every time it replaces ``self.world``, so this only wakes up on an
        actual new snapshot instead of a fixed sleep interval. Raises
        ``asyncio.TimeoutError`` if no matching snapshot arrives in time.
        """
        async def _wait() -> None:
            while not predicate(self.world):
                self._world_updated.clear()
                if predicate(self.world):
                    return
                await self._world_updated.wait()

        await asyncio.wait_for(_wait(), timeout=timeout)
        return self.world

    async def sync(self, *, timeout: float = 5.0) -> WorldView:
        """Send ``sync`` and wait for the fresh state it triggers.

        ``sync`` carries no ``request_id``, so it can't be correlated through
        ``PendingRequests`` like a trading command; the only signal that it
        was answered is ``snapshot_sequence`` advancing past what it was
        when this was called.
        """
        previous_sequence = self.world.snapshot_sequence
        await self._connection.send(codec.build_sync(run_id=self.world.run_id))
        return await self.wait_for(
            lambda world: world.snapshot_sequence > previous_sequence, timeout=timeout
        )


async def open_session(connection: BazaarConnection) -> ClientSession:
    """Complete the readiness handshake and start a ``ClientSession``'s pump loop."""
    messages = connection.messages()
    state = await perform_readiness_handshake(connection, messages)
    logger.info("readiness confirmed for run_id=%s", state.run_id)
    session = ClientSession(connection, messages, WorldView.from_state(state))
    session.start_pump()
    return session


async def run(config: ClientConfig) -> None:
    """Connect, complete the handshake, and log messages until disconnected."""
    async with BazaarConnection(config) as connection:
        messages = connection.messages()
        state = await perform_readiness_handshake(connection, messages)
        logger.info(
            "readiness confirmed for run_id=%s; entering continuous receive loop",
            state.run_id,
        )
        async for message in messages:
            logger.info("received %s", describe_server_message(message))
