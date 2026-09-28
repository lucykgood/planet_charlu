"""Cooperative trading from public advertisements and our private inventory.

Advertisements are claims, not access to other planets' inventories. No strategy
can guarantee their survival or force another team's client to accept an offer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from planet_charlu import codec
from planet_charlu.commands import generate_request_id
from planet_charlu.domain.offers import Offer
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.session import ClientSession

logger = logging.getLogger(__name__)
RESOURCES = tuple(Resource)

# Policy choices; server limits can further reduce these values.
RESERVE_TICKS = 3
TARGET_TICKS = 6
ADVERTISEMENT_TTL_TICKS = 10
ADVERTISEMENT_REFRESH_TICKS = 3
OFFER_TTL_TICKS = 2
MAX_TRADE_AMOUNT = 3
MAX_GIFT_AMOUNT = 2
COMMAND_TIMEOUT_SECONDS = 15


def quantity(bundle: Bundle, resource: Resource) -> int:
    return getattr(bundle, resource.value)


def bundle_of(resource: Resource, amount: int) -> Bundle:
    return Bundle(**{resource.value: amount})


def scaled(bundle: Bundle, ticks: int) -> Bundle:
    return Bundle(
        **{resource.value: quantity(bundle, resource) * ticks for resource in RESOURCES}
    )


def _should_accept(
    offer: Offer,
    *,
    available: Bundle,
    reserve: Bundle,
    needs: Bundle,
    surplus: Bundle,
    specialty: Resource,
) -> bool:
    """Check affordability, reserve protection, and value of an incoming offer."""
    # Incoming give/receive are from the other planet's perspective. We must
    # afford its receive bundle before counting any promised incoming resources.
    if not available.covers(offer.receive):
        return False
    after = available.minus(offer.receive) + offer.give
    safe = all(
        quantity(after, resource)
        >= min(quantity(available, resource), quantity(reserve, resource))
        for resource in RESOURCES
    )
    useful = any(
        quantity(needs, resource) > 0
        and quantity(offer.give, resource) > quantity(offer.receive, resource)
        for resource in RESOURCES
    )
    help_cost = quantity(offer.receive, specialty)
    helping = (
        offer.give.is_zero()
        and 0 < help_cost <= MAX_GIFT_AMOUNT
        and offer.receive == bundle_of(specialty, help_cost)
        and surplus.covers(offer.receive)
    )
    return safe and (offer.is_gift() or useful or helping)


@dataclass(frozen=True)
class Decision:
    """One command, with a local deduplication key and a reason for the log."""

    key: str
    reason: str
    message: pb.ClientMessage

    @property
    def request_id(self) -> str:
        return getattr(self.message, self.message.WhichOneof("message")).request_id


class CooperativeStrategy:
    """Choose bounded trades while protecting upkeep and rotating partners.

    Production is spendable only after it arrives. Outstanding offers also
    retain their payment and upkeep until expiration in our local accounting.
    """

    def __init__(self) -> None:
        self.tick = -1
        self.attempted: set[str] = set()
        self.sent_this_tick = 0
        self.sent_total = 0
        self.retry_tick = 0
        self.partner_turn: dict[str, int] = {}
        self.last_ad_tick = -100

    def choose(self, world: WorldView) -> Decision | None:
        """Select the first eligible action in policy order without sending it.

        Call record() before sending the returned decision. Attempts are scoped
        to a tick so rejected commands cannot cause a tight retry loop.
        """
        if world.tick != self.tick:
            self.tick = world.tick
            self.attempted.clear()
            self.sent_this_tick = 0
        rules = world.rules
        if (
            not world.is_running()
            or world.self.health == 0
            or world.self.failed_once
            or world.tick < self.retry_tick
            or self.sent_this_tick >= rules.new_commands_per_station_per_tick
            or max(self.sent_total, len(world.request_results))
            >= rules.max_request_records_per_station
        ):
            return None
        remaining = max(0, rules.duration_ticks - world.tick)
        if not remaining:
            return None

        reserve = scaled(world.self.upkeep_per_tick, min(RESERVE_TICKS, remaining))
        target = scaled(world.self.upkeep_per_tick, min(TARGET_TICKS, remaining))
        outgoing = [
            offer for offer in world.open_offers_from_me()
            if not offer.is_expired_by(world.tick)
        ]
        committed = Bundle.zero()
        for offer in outgoing:
            committed += offer.give
        available = world.self.inventory.saturating_subtract(committed)
        surplus = available.saturating_subtract(reserve)
        needs = target.saturating_subtract(available)
        specialty = world.self.specialty
        common = dict(run_id=world.run_id, request_id=generate_request_id())

        def decision(
            key: str, reason: str, message: pb.ClientMessage
        ) -> Decision | None:
            if key in self.attempted:
                return None
            if len(codec.encode_client_message(message)) > rules.max_command_bytes:
                return None
            return Decision(key, reason, message)

        # Release unsafe commitments before taking on more obligations.
        if not world.self.inventory.saturating_subtract(reserve).covers(committed):
            for offer in outgoing:
                result = decision(
                    "withdraw:" + offer.offer_id,
                    "release stock needed for upkeep",
                    codec.build_withdraw(**common, object_id=offer.offer_id),
                )
                if result:
                    return result
            return None

        incoming = sorted(
            world.open_offers_to_me(),
            key=lambda offer: (not offer.is_gift(), offer.created_tick, offer.offer_id),
        )
        for offer in incoming:
            if offer.is_expired_by(world.tick):
                continue
            if _should_accept(
                offer,
                available=available,
                reserve=reserve,
                needs=needs,
                surplus=surplus,
                specialty=specialty,
            ):
                result = decision(
                    "accept:" + offer.offer_id,
                    "accept safe gift, useful exchange, or help request",
                    codec.build_accept(**common, offer_id=offer.offer_id),
                )
                if result:
                    return result

        selling = frozenset(
            resource for resource in RESOURCES if quantity(surplus, resource) > 0
        )
        seeking = frozenset(
            resource for resource in RESOURCES if quantity(needs, resource) > 0
        )
        own_ads = [
            ad for ad in world.advertisements
            if ad.posted_by(world.self_station_id)
            and ad.is_active()
            and not ad.is_expired_by(world.tick)
        ]
        ad = own_ads[0] if own_ads else None
        refresh = ad is None or ad.expires_tick <= world.tick + 1
        changed = ad is not None and (ad.selling != selling or ad.seeking != seeking)
        refresh_due = world.tick >= self.last_ad_tick + ADVERTISEMENT_REFRESH_TICKS
        if (selling or seeking) and (refresh or (changed and refresh_due)):
            ttl = min(ADVERTISEMENT_TTL_TICKS, rules.max_publication_ttl_ticks, remaining)
            if ttl > 0:
                result = decision(
                    "advertise",
                    "publish surplus and supply needs",
                    codec.build_advertise(
                        **common,
                        selling=sorted(selling, key=lambda resource: resource.value),
                        seeking=sorted(seeking, key=lambda resource: resource.value),
                        expires_tick=world.tick + ttl,
                    ),
                )
                if result:
                    return result

        if len(outgoing) >= rules.max_open_outgoing_offers:
            return None
        ttl = min(OFFER_TTL_TICKS, rules.max_offer_ttl_ticks, remaining)
        if ttl <= 0:
            return None
        # A peer may accept later: retain upkeep across the offer's lifetime
        # in addition to the reserve protected at the time of this decision.
        spendable = available.saturating_subtract(
            scaled(world.self.upkeep_per_tick, min(RESERVE_TICKS + ttl, remaining))
        )
        busy = {offer.recipient_id for offer in outgoing}
        ads = [
            ad for ad in world.advertisements
            if ad.station_id != world.self_station_id
            and ad.station_id not in busy
            and ad.is_active()
            and not ad.is_expired_by(world.tick)
            and (not world.directory or ad.station_id in world.directory)
        ]
        ads.sort(key=lambda ad: (self.partner_turn.get(ad.station_id, -1), ad.station_id))

        # Seek reciprocal exchanges before gifts, prioritizing ticks of supply.
        wanted = sorted(
            seeking,
            key=lambda resource: quantity(available, resource)
            / max(1, quantity(world.self.upkeep_per_tick, resource)),
        )
        for ad in ads:
            for need in wanted:
                if need not in ad.selling:
                    continue
                payments = sorted(
                    ad.seeking - {need},
                    key=lambda resource: (resource != specialty, resource.value),
                )
                for payment in payments:
                    amount = min(
                        MAX_TRADE_AMOUNT, quantity(spendable, payment), quantity(needs, need)
                    )
                    if amount <= 0:
                        continue
                    result = decision(
                        "partner:" + ad.station_id,
                        f"trade {payment.value} for needed {need.value} with {ad.station_id}",
                        codec.build_offer(
                            **common,
                            recipient_id=ad.station_id,
                            give=bundle_of(payment, amount),
                            receive=bundle_of(need, amount),
                            expires_tick=world.tick + ttl,
                        ),
                    )
                    if result:
                        return result

        for ad in ads:
            amount = min(MAX_GIFT_AMOUNT, quantity(spendable, specialty))
            if specialty not in ad.seeking or amount <= 0:
                continue
            result = decision(
                "partner:" + ad.station_id,
                f"share surplus {specialty.value} with {ad.station_id}",
                codec.build_offer(
                    **common,
                    recipient_id=ad.station_id,
                    give=bundle_of(specialty, amount),
                    receive=Bundle.zero(),
                    expires_tick=world.tick + ttl,
                ),
            )
            if result:
                return result
        return None

    def record(self, decision: Decision) -> None:
        """Count an attempted send even if the server later rejects it."""
        self.attempted.add(decision.key)
        self.sent_this_tick += 1
        self.sent_total += 1
        if decision.key.startswith("partner:"):
            self.partner_turn[decision.key.split(":", 1)[1]] = self.sent_total
        if decision.key == "advertise":
            self.last_ad_tick = self.tick


async def run_trading(session: ClientSession) -> WorldView:
    """Send decisions and reconcile snapshots until the run or station stops."""
    strategy = CooperativeStrategy()
    strategy.sent_total = len(session.world.request_results)
    logger.info(
        "cooperative trading started: station=%s specialty=%s",
        session.world.self_station_id,
        session.world.self.specialty.value,
    )
    while True:
        world = session.world
        if (
            world.phase in (pb.PHASE_FINISHED, pb.PHASE_ABORTED)
            or world.self.health == 0
            or world.self.failed_once
        ):
            return world
        action = strategy.choose(world)
        if action is None:
            # Waiting for a partner is normal and has no command deadline.
            sequence = world.snapshot_sequence
            await session.wait_for(
                lambda world: world.snapshot_sequence > sequence, timeout=None
            )
            continue
        strategy.record(action)
        logger.info("trading: %s", action.reason)
        try:
            outcome = await asyncio.wait_for(
                session.send(request_id=action.request_id, message=action.message),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            logger.info("trading result: %s request_id=%s", outcome.code_name, outcome.request_id)
            if outcome.code == pb.RESULT_CODE_RATE_LIMITED:
                strategy.retry_tick = max(world.tick + 1, outcome.retry_after_tick or 0)
            # Reconcile inventory/commitments before every next decision.
            # Never blindly resend a command whose settlement is uncertain.
            await session.sync(timeout=COMMAND_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "server did not acknowledge a command or synchronize within "
                f"{COMMAND_TIMEOUT_SECONDS} seconds; stopped to avoid trading on stale inventory"
            ) from exc
