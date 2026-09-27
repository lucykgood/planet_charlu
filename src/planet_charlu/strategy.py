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
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.session import ClientSession

logger = logging.getLogger(__name__)
RESOURCES = tuple(Resource)


def quantity(bundle: Bundle, resource: Resource) -> int:
    return getattr(bundle, resource.value)


def bundle_of(resource: Resource, amount: int) -> Bundle:
    return Bundle(**{resource.value: amount})


def scaled(bundle: Bundle, ticks: int) -> Bundle:
    return Bundle(**{r.value: quantity(bundle, r) * ticks for r in RESOURCES})


@dataclass(frozen=True)
class Decision:
    key: str
    reason: str
    message: pb.ClientMessage

    @property
    def request_id(self) -> str:
        return getattr(self.message, self.message.WhichOneof("message")).request_id


class CooperativeStrategy:
    """Small, bounded trades; three ticks protected, six ticks desired.

    Production is deliberately not counted as spendable until it arrives.
    Outstanding offers also reserve their payment and upkeep until expiration.
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
        if world.tick != self.tick:
            self.tick = world.tick
            self.attempted.clear()
            self.sent_this_tick = 0
        rules = world.rules
        if (not world.is_running() or world.self.health == 0 or world.self.failed_once
                or world.tick < self.retry_tick
                or self.sent_this_tick >= rules.new_commands_per_station_per_tick
                or max(self.sent_total, len(world.request_results)) >= rules.max_request_records_per_station):
            return None
        remaining = max(0, rules.duration_ticks - world.tick)
        if not remaining:
            return None
        reserve = scaled(world.self.upkeep_per_tick, min(3, remaining))
        target = scaled(world.self.upkeep_per_tick, min(6, remaining))
        outgoing = [o for o in world.open_offers_from_me() if not o.is_expired_by(world.tick)]
        committed = Bundle.zero()
        for offer in outgoing:
            committed += offer.give
        available = world.self.inventory.saturating_subtract(committed)
        surplus = available.saturating_subtract(reserve)
        needs = target.saturating_subtract(available)
        specialty = world.self.specialty
        common = dict(run_id=world.run_id, request_id=generate_request_id())

        def decision(key, reason, message):
            if key in self.attempted or len(codec.encode_client_message(message)) > rules.max_command_bytes:
                return None
            return Decision(key, reason, message)

        # Release unsafe commitments before taking on more obligations.
        if not world.self.inventory.saturating_subtract(reserve).covers(committed):
            for offer in outgoing:
                result = decision('withdraw:' + offer.offer_id, 'release stock needed for upkeep',
                                  codec.build_withdraw(**common, object_id=offer.offer_id))
                if result:
                    return result
            return None

        # Incoming give/receive are from the OTHER planet's perspective.
        incoming = sorted(world.open_offers_to_me(), key=lambda o: (not o.is_gift(), o.created_tick, o.offer_id))
        for offer in incoming:
            if offer.is_expired_by(world.tick) or not available.covers(offer.receive):
                continue
            after = available.minus(offer.receive) + offer.give
            safe = all(quantity(after, r) >= min(quantity(available, r), quantity(reserve, r)) for r in RESOURCES)
            useful = any(quantity(needs, r) > 0 and quantity(offer.give, r) > quantity(offer.receive, r) for r in RESOURCES)
            # Also fulfill small help requests paid only from surplus specialty.
            help_cost = quantity(offer.receive, specialty)
            helping = (offer.give.is_zero() and 0 < help_cost <= 2
                       and offer.receive == bundle_of(specialty, help_cost)
                       and surplus.covers(offer.receive))
            if safe and (offer.is_gift() or useful or helping):
                result = decision('accept:' + offer.offer_id, 'accept safe gift, useful exchange, or help request',
                                  codec.build_accept(**common, offer_id=offer.offer_id))
                if result:
                    return result

        selling = frozenset(r for r in RESOURCES if quantity(surplus, r) > 0)
        seeking = frozenset(r for r in RESOURCES if quantity(needs, r) > 0)
        own_ads = [a for a in world.advertisements if a.posted_by(world.self_station_id)
                   and a.is_active() and not a.is_expired_by(world.tick)]
        ad = own_ads[0] if own_ads else None
        refresh = ad is None or ad.expires_tick <= world.tick + 1
        changed = ad is not None and (ad.selling != selling or ad.seeking != seeking)
        if (selling or seeking) and (refresh or (changed and world.tick >= self.last_ad_tick + 3)):
            ttl = min(10, rules.max_publication_ttl_ticks, remaining)
            if ttl > 0:
                result = decision('advertise', 'publish surplus and supply needs', codec.build_advertise(
                    **common, selling=sorted(selling, key=lambda r: r.value),
                    seeking=sorted(seeking, key=lambda r: r.value), expires_tick=world.tick + ttl))
                if result:
                    return result

        if len(outgoing) >= rules.max_open_outgoing_offers:
            return None
        ttl = min(2, rules.max_offer_ttl_ticks, remaining)
        if ttl <= 0:
            return None
        # Retain upkeep across the offer's lifetime, in addition to the reserve.
        spendable = available.saturating_subtract(scaled(world.self.upkeep_per_tick, min(3 + ttl, remaining)))
        busy = {o.recipient_id for o in outgoing}
        ads = [a for a in world.advertisements if a.station_id != world.self_station_id
               and a.station_id not in busy and a.is_active() and not a.is_expired_by(world.tick)
               and (not world.directory or a.station_id in world.directory)]
        ads.sort(key=lambda a: (self.partner_turn.get(a.station_id, -1), a.station_id))
        # First seek reciprocal exchanges, prioritizing the shortest supply.
        wanted = sorted(seeking, key=lambda r: quantity(available, r) / max(1, quantity(world.self.upkeep_per_tick, r)))
        for ad in ads:
            for need in wanted:
                if need not in ad.selling:
                    continue
                payments = sorted(ad.seeking - {need}, key=lambda r: (r != specialty, r.value))
                for payment in payments:
                    amount = min(3, quantity(spendable, payment), quantity(needs, need))
                    if amount <= 0:
                        continue
                    result = decision('partner:' + ad.station_id, f'trade {payment.value} for needed {need.value} with {ad.station_id}',
                        codec.build_offer(**common, recipient_id=ad.station_id,
                            give=bundle_of(payment, amount), receive=bundle_of(need, amount),
                            expires_tick=world.tick + ttl))
                    if result:
                        return result
        # Help advertised specialty shortages with small gifts, rotating partners.
        for ad in ads:
            amount = min(2, quantity(spendable, specialty))
            if specialty not in ad.seeking or amount <= 0:
                continue
            result = decision('partner:' + ad.station_id, f'share surplus {specialty.value} with {ad.station_id}',
                codec.build_offer(**common, recipient_id=ad.station_id, give=bundle_of(specialty, amount),
                                  receive=Bundle.zero(), expires_tick=world.tick + ttl))
            if result:
                return result
        return None

    def record(self, decision: Decision) -> None:
        self.attempted.add(decision.key)
        self.sent_this_tick += 1
        self.sent_total += 1
        if decision.key.startswith('partner:'):
            self.partner_turn[decision.key.split(':', 1)[1]] = self.sent_total
        if decision.key == 'advertise':
            self.last_ad_tick = self.tick


async def run_trading(session: ClientSession) -> WorldView:
    strategy = CooperativeStrategy()
    strategy.sent_total = len(session.world.request_results)
    logger.info('cooperative trading started: station=%s specialty=%s',
                session.world.self_station_id, session.world.self.specialty.value)
    while True:
        world = session.world
        if world.phase in (pb.PHASE_FINISHED, pb.PHASE_ABORTED) or world.self.health == 0 or world.self.failed_once:
            return world
        action = strategy.choose(world)
        if action is None:
            # Waiting for a partner is normal; it has no five-second deadline.
            sequence = world.snapshot_sequence
            await session.wait_for(lambda w: w.snapshot_sequence > sequence, timeout=None)
            continue
        strategy.record(action)
        logger.info('trading: %s', action.reason)
        try:
            outcome = await asyncio.wait_for(session.send(request_id=action.request_id, message=action.message), timeout=15)
            logger.info('trading result: %s request_id=%s', outcome.code_name, outcome.request_id)
            if outcome.code == pb.RESULT_CODE_RATE_LIMITED:
                strategy.retry_tick = max(world.tick + 1, outcome.retry_after_tick or 0)
            # Reconcile server inventory/commitments before every next decision.
            # Never blindly resend a command whose settlement is uncertain.
            await session.sync(timeout=15)
        except asyncio.TimeoutError as exc:
            raise RuntimeError('server did not acknowledge a command or synchronize within 15 seconds; stopped to avoid trading on stale inventory') from exc
