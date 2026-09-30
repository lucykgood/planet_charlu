"""Safe, concise logging of decoded server messages.

Access tokens live only in the WebSocket ``Authorization`` header and are
never part of a ``ServerMessage``, so summaries built here cannot leak one.
Callers still must not log ``ClientConfig.token`` or connection headers
directly.
"""

from __future__ import annotations

import logging

from planet_charlu.domain.offers import Offer
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.resources import Bundle, Resource, resource_from_wire
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2

RESERVE_TICKS = 3
TICK_SEPARATOR = "-" * 70


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(module)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _humanize(name: str, prefix: str) -> str:
    """``"RESULT_CODE_RATE_LIMITED"`` -> ``"rate limited"``."""
    return name.removeprefix(prefix).replace("_", " ").lower()


def humanize_phase(phase: int) -> str:
    return _humanize(bazaar_pb2.Phase.Name(phase), "PHASE_")


def humanize_result_code(code: int) -> str:
    return _humanize(bazaar_pb2.ResultCode.Name(code), "RESULT_CODE_")


def describe_server_message(message: bazaar_pb2.ServerMessage) -> str:
    """Return a one-line, safe-to-log summary of a decoded server message."""
    kind = message.WhichOneof("message")

    if kind == "state":
        state = message.state
        inventory = state.self.inventory
        return (
            f"state seq={state.snapshot_sequence} world_version={state.world_version} "
            f"tick={state.tick} phase={humanize_phase(state.phase)} "
            f"self={state.self_station_id} health={state.self.health} "
            f"specialty={_humanize(bazaar_pb2.Resource.Name(state.self.specialty), 'RESOURCE_')} "
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


def _fmt_bundle(bundle: Bundle) -> str:
    parts = [f"{getattr(bundle, r.value)} {r.value}" for r in Resource if getattr(bundle, r.value) > 0]
    return " + ".join(parts) if parts else "nothing"


def _fmt_reserves(world: WorldView) -> str:
    parts = []
    for resource in Resource:
        current = getattr(world.self.inventory, resource.value)
        upkeep = getattr(world.self.upkeep_per_tick, resource.value)
        reserve_target = upkeep * RESERVE_TICKS
        status = "CRITICAL" if current < reserve_target else "ok"
        parts.append(f"{resource.value}={current} (reserve {reserve_target}, {status})")
    return " | ".join(parts)


def format_sent_terms(message: bazaar_pb2.ClientMessage) -> str:
    """The exact terms of an outgoing command, for the human-facing log line.

    ``action.reason`` is a sentence like "trade water for needed food with
    P02" -- true, but it doesn't say how much. This says how much.
    """
    kind = message.WhichOneof("message")
    if kind == "offer":
        body = message.offer.body
        give = Bundle.from_wire(body.give)
        receive = Bundle.from_wire(body.receive)
        return (f"give {_fmt_bundle(give)}, receive {_fmt_bundle(receive)} "
                f"to {body.recipient_id} (expires tick {body.expires_tick})")
    if kind == "accept":
        return f"offer {message.accept.body.offer_id}"
    if kind == "withdraw":
        return f"object {message.withdraw.body.object_id}"
    if kind == "advertise":
        body = message.advertise.body
        selling = ", ".join(resource_from_wire(r).value for r in body.selling.items) or "nothing"
        seeking = ", ".join(resource_from_wire(r).value for r in body.seeking.items) or "nothing"
        return f"selling {selling}, seeking {seeking} (expires tick {body.expires_tick})"
    return kind


def format_result_extra(outcome: CommandOutcome) -> str:
    """Returns ' (object <id>)', ' (transaction <id>)', both, or '' --
    appended to the ok/rejected result line so it's visible which record the
    server actually created, not just that the command succeeded.
    """
    parts = []
    if outcome.object_id:
        parts.append(f"object {outcome.object_id}")
    if outcome.transaction_id:
        parts.append(f"transaction {outcome.transaction_id}")
    return f" ({', '.join(parts)})" if parts else ""


def format_offer_review(world: WorldView, review: list[tuple[Offer, str]]) -> str | None:
    """Incoming offers considered and declined in the most recent ``choose()``
    call -- the "why did it pass" half of the picture ``format_status_report``
    doesn't show on its own (that one only lists what's still open, not what
    was looked at and turned down). Returns ``None`` when there's nothing to
    report, so callers can skip logging an empty line.
    """
    passed = [(offer, reason) for offer, reason in review if reason != "accepted"]
    if not passed:
        return None
    lines = [f"passed on {len(passed)} incoming offer(s):"]
    for offer, reason in passed:
        other, we_would_give, we_would_receive = offer.relative_to(world.self_station_id)
        lines.append(f"  x offer from {other}: we'd give {_fmt_bundle(we_would_give)}, "
                     f"we'd receive {_fmt_bundle(we_would_receive)} -- {reason}")
    return "\n".join(lines)


def format_status_report(world: WorldView, *, recent_trade_limit: int = 5) -> str:
    """A multi-line, human-readable snapshot of our station right now.

    Meant to be logged once per tick so a run's console/log output stays
    readable without cross-referencing raw offer/transaction IDs: current
    epoch, reserves against upkeep, our own unresolved offers/advertisements
    ("pending actions" -- proposals we're waiting on a response to), offers
    open to us, and the most recent settled trades. Leads with
    ``TICK_SEPARATOR``, a horizontal rule, so each tick's block is visually
    unmistakable even scrolling past a decision/result line left over from
    the previous tick.
    """
    self_id = world.self_station_id
    lines = [
        TICK_SEPARATOR,
        f"tick {world.tick} | {humanize_phase(world.phase)} | "
        f"station={self_id} specialty={world.self.specialty.value} health={world.self.health}",
        f"reserves: {_fmt_reserves(world)}",
    ]

    outgoing = [o for o in world.open_offers_from_me() if not o.is_expired_by(world.tick)]
    own_ads = [
        a for a in world.advertisements
        if a.posted_by(self_id) and a.is_active() and not a.is_expired_by(world.tick)
    ]
    if outgoing or own_ads:
        lines.append(f"pending actions: {len(outgoing)} offer(s) awaiting response, "
                     f"{len(own_ads)} active advertisement(s)")
        for offer in outgoing:
            other, we_give, we_receive = offer.relative_to(self_id)
            lines.append(f"  -> offer to {other}: give {_fmt_bundle(we_give)}, "
                         f"receive {_fmt_bundle(we_receive)} (expires tick {offer.expires_tick})")
        for ad in own_ads:
            selling = ", ".join(r.value for r in ad.selling) or "nothing"
            seeking = ", ".join(r.value for r in ad.seeking) or "nothing"
            lines.append(f"  -> advertising: selling {selling}, seeking {seeking} "
                         f"(expires tick {ad.expires_tick})")
    else:
        lines.append("pending actions: none")

    incoming = [o for o in world.open_offers_to_me() if not o.is_expired_by(world.tick)]
    if incoming:
        lines.append(f"open offers to us ({len(incoming)}):")
        for offer in incoming:
            other, we_give, we_receive = offer.relative_to(self_id)
            lines.append(f"  <- offer from {other}: we'd give {_fmt_bundle(we_give)}, "
                         f"we'd receive {_fmt_bundle(we_receive)} (expires tick {offer.expires_tick})")
    else:
        lines.append("open offers to us: none")

    recent = sorted(
        (tx for tx in world.transactions if tx.involves(self_id)),
        key=lambda tx: tx.settled_tick, reverse=True,
    )[:recent_trade_limit]
    if recent:
        lines.append(f"recent trades (last {len(recent)}):")
        for tx in recent:
            other, we_gave, we_received = tx.relative_to(self_id)
            lines.append(f"  tick {tx.settled_tick}: gave {_fmt_bundle(we_gave)}, "
                         f"received {_fmt_bundle(we_received)} <-> {other}")
    else:
        lines.append("recent trades: none yet")

    return "\n".join(lines)
