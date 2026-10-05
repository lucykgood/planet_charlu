"""WebSocket lifecycle: endpoint, authentication, and Protobuf framing.

Owns exactly one connection to the Bazaar server. State tracking and
decision logic live elsewhere; this module only moves bytes.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import InvalidStatus

from planet_charlu import codec
from planet_charlu.config import ClientConfig
from planet_charlu.generated import bazaar_pb2

logger = logging.getLogger(__name__)

REQUIRED_SUBPROTOCOL = "bazaar.protobuf.v2"


class SubprotocolMismatchError(RuntimeError):
    """Raised when the server does not select the required subprotocol."""


class BazaarConnection:
    """A single authenticated WebSocket connection to the Bazaar server."""

    def __init__(self, config: ClientConfig) -> None:
        self._config = config
        self._socket: Optional[ClientConnection] = None

    async def __aenter__(self) -> "BazaarConnection":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self) -> None:
        try:
            socket = await websockets.connect(
                self._config.ws_url,
                additional_headers={"Authorization": f"Bearer {self._config.token}"},
                subprotocols=[REQUIRED_SUBPROTOCOL],
            )
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status == 401:
                hint = (
                    "Authentication failed. Supply the token issued for this server via "
                    "BAZAAR_TOKEN or --token. Local validation-credentials.json tokens "
                    "are for the local validator, not the live server. In Docker, set "
                    "BAZAAR_TOKEN inside the container or pass it with docker compose exec -e BAZAAR_TOKEN."
                )
            elif status == 403:
                hint = "Access denied. Check that your credentials are authorized for this server."
            elif status == 400:
                hint = "Check the assigned station and the required bazaar.protobuf.v2 subprotocol."
            else:
                hint = "Check the WebSocket endpoint and server availability."
            # Don't include response bodies or headers: they may echo credentials.
            raise ConnectionError(f"WebSocket handshake rejected (HTTP {status}). {hint}") from None
        if socket.subprotocol != REQUIRED_SUBPROTOCOL:
            negotiated = socket.subprotocol
            await socket.close()
            raise SubprotocolMismatchError(
                f"server selected subprotocol {negotiated!r}, "
                f"expected {REQUIRED_SUBPROTOCOL!r}"
            )
        self._socket = socket
        logger.info(
            "connected to %s (subprotocol=%s)", self._config.ws_url, socket.subprotocol
        )

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    async def send(self, message: bazaar_pb2.ClientMessage) -> None:
        if self._socket is None:
            raise RuntimeError("not connected")
        await self._socket.send(codec.encode_client_message(message))

    async def messages(self) -> AsyncIterator[bazaar_pb2.ServerMessage]:
        """Yield decoded server messages until the connection closes.

        WebSocket ping/pong frames are handled and acknowledged by the
        underlying ``websockets`` library and never reach this loop; a close
        frame ends the loop (or raises, if the closure was not clean). Only
        binary Protobuf data frames are surfaced here.
        """
        if self._socket is None:
            raise RuntimeError("not connected")

        async for raw in self._socket:
            if isinstance(raw, str):
                raise ValueError(
                    "received a text frame; only binary Protobuf frames are expected"
                )
            yield codec.decode_server_message(raw)

        logger.info(
            "connection closed (code=%s, reason=%r)",
            self._socket.close_code,
            self._socket.close_reason,
        )
