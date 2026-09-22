"""Request ID correlation: matching an async ``result`` back to its command.

The server's ``result`` messages arrive interleaved with ``state`` messages
on the same receive loop and carry only a ``request_id`` -- nothing that
says which call sent it. ``PendingRequests`` is a table of futures keyed by
``request_id``: a caller registers one before sending, and whoever is
running the receive loop resolves it when the matching ``result`` shows up,
so ``send_command`` can look like an ordinary awaitable call.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict

from planet_charlu.connection import BazaarConnection
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.generated import bazaar_pb2

logger = logging.getLogger(__name__)


class DuplicateRequestIdError(RuntimeError):
    """Raised when a request_id is registered while still awaiting a prior result."""


def generate_request_id() -> str:
    return uuid.uuid4().hex


class PendingRequests:
    """Tracks in-flight commands and resolves them as ``result`` messages arrive."""

    def __init__(self) -> None:
        self._pending: Dict[str, "asyncio.Future[CommandOutcome]"] = {}

    def register(self, request_id: str) -> "asyncio.Future[CommandOutcome]":
        if request_id in self._pending:
            raise DuplicateRequestIdError(request_id)
        future: "asyncio.Future[CommandOutcome]" = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        return future

    def resolve(self, outcome: CommandOutcome) -> None:
        future = self._pending.pop(outcome.request_id, None)
        if future is None:
            logger.warning(
                "received result for unknown or already-resolved request_id=%s",
                outcome.request_id,
            )
            return
        if not future.done():
            future.set_result(outcome)


async def send_command(
    connection: BazaarConnection,
    pending: PendingRequests,
    *,
    request_id: str,
    message: bazaar_pb2.ClientMessage,
) -> CommandOutcome:
    """Send ``message`` and await the ``result`` that matches ``request_id``.

    Requires something else (the receive loop) to be concurrently pumping
    ``connection.messages()`` and calling ``pending.resolve()`` for each
    decoded ``result`` -- this only registers the future and sends.
    """
    future = pending.register(request_id)
    await connection.send(message)
    return await future
