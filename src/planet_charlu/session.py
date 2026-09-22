"""Scenario orchestration: the required connection sequence and receive loop.

Steps 1-4 of the required connection sequence (connect, read initial state,
send ready, wait for readiness) live here. Sending trading commands once the
run is confirmed RUNNING is out of scope for this module; it only proves the
handshake and keeps listening.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

from planet_charlu import codec
from planet_charlu.config import ClientConfig
from planet_charlu.connection import BazaarConnection
from planet_charlu.generated import bazaar_pb2
from planet_charlu.logging_utils import describe_server_message

logger = logging.getLogger(__name__)


class HandshakeError(RuntimeError):
    """Raised when the initial state/readiness exchange does not behave as required."""


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
