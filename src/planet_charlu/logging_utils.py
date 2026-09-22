"""Safe, concise logging of decoded server messages.

Access tokens live only in the WebSocket ``Authorization`` header and are
never part of a ``ServerMessage``, so summaries built here cannot leak one.
Callers still must not log ``ClientConfig.token`` or connection headers
directly.
"""

from __future__ import annotations

import logging

from planet_charlu.generated import bazaar_pb2


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def describe_server_message(message: bazaar_pb2.ServerMessage) -> str:
    """Return a one-line, safe-to-log summary of a decoded server message."""
    kind = message.WhichOneof("message")

    if kind == "state":
        state = message.state
        inventory = state.self.inventory
        return (
            f"state seq={state.snapshot_sequence} world_version={state.world_version} "
            f"tick={state.tick} phase={bazaar_pb2.Phase.Name(state.phase)} "
            f"self={state.self_station_id} health={state.self.health} "
            f"specialty={bazaar_pb2.Resource.Name(state.self.specialty)} "
            f"inventory=(water={inventory.water},food={inventory.food},"
            f"components={inventory.components})"
        )
    if kind == "result":
        result = message.result
        return (
            f"result request_id={result.request_id} ok={result.ok} "
            f"code={bazaar_pb2.ResultCode.Name(result.code)}"
        )
    if kind == "readiness":
        readiness = message.readiness
        return (
            f"readiness run_id={readiness.run_id} ready={readiness.ready} "
            f"seq={readiness.snapshot_sequence}"
        )
    if kind == "protocol_error":
        error = message.protocol_error
        return (
            f"protocol_error code={bazaar_pb2.ControlCode.Name(error.code)} "
            f"close_session={error.close_session}"
        )
    return f"unset server message (kind={kind!r})"
