"""Self-sufficient trading from public advertisements and our private inventory.

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
from planet_charlu.logging_utils import (
    format_offer_review,
    format_result_extra,
    format_sent_terms,
    format_status_report,
    humanize_result_code,
)
from planet_charlu.session import ClientSession, NotRunningError
from planet_charlu.structured_log import RunLog

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


class SelfSufficientStrategy:
    """Trade only for the two resources we do not produce; never give away
    either of them; pay only in our specialty, which production replaces.

    Every trade this station proposes or accepts must be favorable or equal
    for us: at least as many units come in as our specialty units pay out.
    Against an equally disciplined partner that converges to exact 1:1
    barter, which is the default this strategy itself proposes.

    A roster of confirmed suppliers -- station_id -> the resource(s) it has
    actually delivered, learned from settled ``world.transactions`` rather
    than from advertisements (a claim, not proof of stock) -- is preferred
    over blind advertisement-scanning once at least one supplier is
    confirmed for a resource. Advertisements remain the only discovery
    mechanism for a resource with no confirmed supplier yet.

    Free gifts of anything are always accepted regardless of roster or
    resource. If our own specialty ever falls below the three-tick reserve
    (production can fall short of its own upkeep), that is a survival
    exception: we seek and accept trades for it too, funded from whichever
    other resource we can spare, exactly like any other critical resource.

    ``max_request_records_per_station`` is a lifetime budget, not a
    per-tick one -- routine advertising and offering can exhaust it long
    before the run ends if left unpaced, leaving no capacity for even a
    free gift or a safety withdrawal for however many ticks remain.
    EMERGENCY_RESERVE keeps a small floor of that budget off-limits to
    routine spending (new ads, non-critical seeking, specialty gifts) once
    it starts running low; withdrawing unsafe commitments, accepting a
    gift, and rescuing a critical resource are never rationed by it.
    """
    EMERGENCY_RESERVE = 4
    RESERVE_TICKS = 3
    TARGET_TICKS = 6
    SEEK_SPECIALTY_TO_TARGET = False

    def can_gift(self, world: WorldView, available: Bundle, ttl: int) -> bool:
        return True

    def __init__(self) -> None:
        self.tick = -1
        self.attempted: set[str] = set()
        self.sent_this_tick = 0
        self.sent_total = 0
        self.retry_tick = 0
        self.partner_turn: dict[str, int] = {}
        self.last_ad_tick = -100
        self.known_suppliers: dict[str, set[Resource]] = {}
        self.seen_transactions: set[str] = set()
        # Every incoming offer this call's loop actually looked at, with why
        # it was accepted or passed on -- repopulated fresh on every
        # choose() call so a caller can log it regardless of whether this
        # call ends up returning a Decision.
        self.last_offer_review: list[tuple[Offer, str]] = []

    def _learn_suppliers(self, world: WorldView) -> None:
        """Record which resource each partner actually delivered.

        Built from settled transactions -- proof stock moved -- not from
        advertisements, which only claim it.
        """
        for tx in world.transactions:
            if tx.transaction_id in self.seen_transactions:
                continue
            self.seen_transactions.add(tx.transaction_id)
            if tx.proposer_id == world.self_station_id:
                other, delivered_to_us = tx.recipient_id, tx.receive
            elif tx.recipient_id == world.self_station_id:
                other, delivered_to_us = tx.proposer_id, tx.give
            else:
                continue
            delivered = frozenset(r for r in RESOURCES if quantity(delivered_to_us, r) > 0)
            if delivered:
                self.known_suppliers.setdefault(other, set()).update(delivered)

    def confirmed_suppliers_of(self, resource: Resource) -> frozenset[str]:
        return frozenset(station_id for station_id, resources in self.known_suppliers.items()
                          if resource in resources)

    def choose(self, world: WorldView) -> Decision | None:
        self._learn_suppliers(world)
        self.last_offer_review = []
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
        reserve = scaled(world.self.upkeep_per_tick, min(self.RESERVE_TICKS, remaining))
        emergency_reserve = scaled(world.self.upkeep_per_tick, min(3, remaining))
        target = scaled(world.self.upkeep_per_tick, min(self.TARGET_TICKS, remaining))
        outgoing = [o for o in world.open_offers_from_me() if not o.is_expired_by(world.tick)]
        committed = Bundle.zero()
        for offer in outgoing:
            committed += offer.give
        available = world.self.inventory.saturating_subtract(committed)
        surplus = available.saturating_subtract(reserve)
        needs = target.saturating_subtract(available)
        critical = frozenset(r for r in RESOURCES if quantity(available, r) < quantity(emergency_reserve, r))
        specialty = world.self.specialty
        not_produced = frozenset(RESOURCES) - {specialty}
        common = dict(run_id=world.run_id, request_id=generate_request_id())
        budget_left = rules.max_request_records_per_station - max(self.sent_total, len(world.request_results))
        routine_ok = budget_left > self.EMERGENCY_RESERVE

        def decision(key, reason, message):
            if key in self.attempted or len(codec.encode_client_message(message)) > rules.max_command_bytes:
                return None
            return Decision(key, reason, message)

        # Release unsafe commitments before taking on more obligations.
        commitment_reserve = reserve
        if any(any(r in critical and quantity(o.receive, r) > quantity(o.give, r)
                   for r in RESOURCES) for o in outgoing):
            commitment_reserve = emergency_reserve
        if not world.self.inventory.saturating_subtract(commitment_reserve).covers(committed):
            for offer in outgoing:
                result = decision('withdraw:' + offer.offer_id, 'release stock needed for upkeep',
                                  codec.build_withdraw(**common, object_id=offer.offer_id))
                if result:
                    return result
            return None

        # The default seeks specialty only in an emergency; the conservative
        # policy also replenishes it to the routine target.
        seeking = frozenset(r for r in not_produced if quantity(needs, r) > 0)
        if specialty in critical or (self.SEEK_SPECIALTY_TO_TARGET and quantity(needs, specialty) > 0):
            seeking = seeking | {specialty}

        # Incoming give/receive are from the OTHER planet's perspective.
        def rescues_critical(offer):
            return any(r in critical and quantity(offer.give, r) > quantity(offer.receive, r) for r in RESOURCES)

        incoming = sorted(world.open_offers_to_me(),
                          key=lambda o: (not rescues_critical(o), not o.is_gift(), o.created_tick, o.offer_id))
        for offer in incoming:
            if offer.is_expired_by(world.tick):
                self.last_offer_review.append((offer, 'expired'))
                continue
            if not available.covers(offer.receive):
                self.last_offer_review.append((offer, 'insufficient stock to cover what it asks'))
                continue
            after = available.minus(offer.receive) + offer.give
            payment_reserve = emergency_reserve if rescues_critical(offer) else reserve
            safe = all(quantity(after, r) >= min(quantity(available, r), quantity(payment_reserve, r)) for r in RESOURCES)
            if not safe:
                self.last_offer_review.append((offer, 'would breach the upkeep reserve'))
                continue
            if offer.is_gift():
                result = decision('accept:' + offer.offer_id, 'accept free gift',
                                  codec.build_accept(**common, offer_id=offer.offer_id))
                if result:
                    self.last_offer_review.append((offer, 'accepted'))
                    return result
                self.last_offer_review.append((offer, 'already attempted this tick, or message too large'))
                continue
            # The default pays only in specialty. Conservative specialty
            # replenishment may use other stock under the same safety check.
            pays_only_specialty = all(r == specialty or quantity(offer.receive, r) == 0 for r in RESOURCES)
            if self.SEEK_SPECIALTY_TO_TARGET and specialty in seeking:
                pays_only_specialty = pays_only_specialty or (
                    quantity(offer.receive, specialty) == 0
                    and quantity(offer.give, specialty) > 0)
            units_in = sum(quantity(offer.give, r) for r in RESOURCES)
            units_out = sum(quantity(offer.receive, r) for r in RESOURCES)
            favorable_or_equal = units_in >= units_out
            nets_needed = any(r in seeking and quantity(offer.give, r) > quantity(offer.receive, r) for r in RESOURCES)
            # A rescue is never rationed; a routine favorable trade is, once
            # the emergency reserve is all that's left of the lifetime budget.
            if pays_only_specialty and favorable_or_equal and nets_needed and (routine_ok or rescues_critical(offer)):
                result = decision('accept:' + offer.offer_id, 'accept favorable-or-equal trade for a resource we need',
                                  codec.build_accept(**common, offer_id=offer.offer_id))
                if result:
                    self.last_offer_review.append((offer, 'accepted'))
                    return result
                self.last_offer_review.append((offer, 'already attempted this tick, or message too large'))
            elif not pays_only_specialty:
                self.last_offer_review.append((offer, 'would require payment beyond our specialty'))
            elif not favorable_or_equal:
                self.last_offer_review.append((offer, 'unfavorable: would give more than we receive'))
            elif not nets_needed:
                self.last_offer_review.append((offer, "doesn't net a resource we're seeking"))
            else:
                self.last_offer_review.append((offer, 'routine budget exhausted; not a rescue'))

        # We only ever advertise our specialty as surplus; the other two are
        # never for sale, no matter how much of them we happen to be holding.
        selling = frozenset({specialty}) if quantity(surplus, specialty) > 0 else frozenset()
        own_ads = [a for a in world.advertisements if a.posted_by(world.self_station_id)
                   and a.is_active() and not a.is_expired_by(world.tick)]
        ad = own_ads[0] if own_ads else None
        refresh = ad is None or ad.expires_tick <= world.tick + 1
        changed = ad is not None and (ad.selling != selling or ad.seeking != seeking)
        if routine_ok and (selling or seeking) and (refresh or (changed and world.tick >= self.last_ad_tick + 3)):
            ttl = min(10, rules.max_publication_ttl_ticks, remaining)
            if ttl > 0:
                result = decision('advertise', 'publish specialty surplus and unproduced-resource needs', codec.build_advertise(
                    **common, selling=sorted(selling, key=lambda r: r.value),
                    seeking=sorted(seeking, key=lambda r: r.value), expires_tick=world.tick + ttl))
                if result:
                    return result

        if len(outgoing) >= rules.max_open_outgoing_offers:
            return None
        # Give a partner a real window to notice and act on the offer before
        # it expires -- too short a TTL just forces repeated re-offering
        # (and repeated budget spend) without giving them more of a chance
        # to accept.
        ttl = min(5, rules.max_offer_ttl_ticks, remaining)
        if ttl <= 0:
            return None
        # Retain upkeep across the offer's lifetime, in addition to the reserve.
        spendable = available.saturating_subtract(scaled(world.self.upkeep_per_tick, min(self.RESERVE_TICKS + ttl, remaining)))
        busy = {o.recipient_id for o in outgoing}
        ads = [a for a in world.advertisements if a.station_id != world.self_station_id
               and a.station_id not in busy and a.is_active() and not a.is_expired_by(world.tick)
               and (not world.directory or a.station_id in world.directory)]
        ads.sort(key=lambda a: (self.partner_turn.get(a.station_id, -1), a.station_id))
        # Prioritize critical resources (below the 3-tick reserve) ahead of
        # everything else, then the shortest supply.
        wanted = sorted(seeking, key=lambda r: (r not in critical,
                         quantity(available, r) / max(1, quantity(world.self.upkeep_per_tick, r))))
        for need in wanted:
            urgent = need in critical
            if not urgent and not routine_ok:
                # Recovering a critical resource is never rationed; seeking
                # one that's merely below the routine target is, once
                # the emergency reserve is all that's left of the budget.
                continue
            # An ad is only a claim; once a partner has actually delivered
            # this resource, stop trusting anyone else's unconfirmed claim.
            confirmed = self.confirmed_suppliers_of(need)
            pool = [a for a in ads if need in a.selling and (not confirmed or a.station_id in confirmed)]
            if need == specialty:
                # Can't fund buying specialty with itself, so pay from
                # whichever other resource we can spare.
                payment_options = tuple(sorted(not_produced, key=lambda r: r.value))
            else:
                payment_options = (specialty,)
            pay_pool = available.saturating_subtract(emergency_reserve) if need in critical else spendable
            cap = 6 if need in critical else 3
            for candidate_ad in pool:
                for payment in payment_options:
                    if payment not in candidate_ad.seeking:
                        continue
                    amount = min(cap, quantity(pay_pool, payment), quantity(needs, need))
                    if amount <= 0:
                        continue
                    reason = f'trade {payment.value} for needed {need.value} with {candidate_ad.station_id}'
                    if candidate_ad.station_id in confirmed:
                        reason += ' (confirmed supplier)'
                    result = decision('partner:' + candidate_ad.station_id, reason,
                        codec.build_offer(**common, recipient_id=candidate_ad.station_id,
                            give=bundle_of(payment, amount), receive=bundle_of(need, amount),
                            expires_tick=world.tick + ttl))
                    if result:
                        return result
        # Help advertised specialty shortages with small gifts, rotating
        # partners -- pure generosity, so it's the first thing rationed.
        if routine_ok and self.can_gift(world, available, ttl):
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


@dataclass(frozen=True)
class TradingPolicy:
    """Supply windows in simulation ticks, independent of clock speed."""

    reserve_ticks: int = 3
    refill_ticks: int = 6
    target_ticks: int = 15

    def __post_init__(self) -> None:
        values = (self.reserve_ticks, self.refill_ticks, self.target_ticks)
        if any(type(value) is not int for value in values) or not 0 < self.reserve_ticks < self.refill_ticks < self.target_ticks:
            raise ValueError("policy requires 0 < reserve_ticks < refill_ticks < target_ticks")


class ConservativeTradingStrategy:
    """Keep a supply buffer; exchange specialty stock at equal unit prices.

    Refill in batches, accept useful fair exchanges to help neighbors, and
    distribute proposals among advertised suppliers. No gifts or supplier
    whitelist are needed when all nine planets use this policy.
    """

    def __init__(self, policy: TradingPolicy | None = None) -> None:
        self.policy = policy or TradingPolicy()
        self.tick = -1
        self.attempted: set[str] = set()
        self.sent_this_tick = 0
        self.sent_total = 0
        self.retry_tick = 0
        self.partner_turn: dict[str, int] = {}
        self.last_offer_review: list[tuple[Offer, str]] = []

    def record(self, action: Decision) -> None:
        self.attempted.add(action.key)
        self.sent_this_tick += 1
        self.sent_total += 1
        if action.key.startswith('partner:'):
            self.partner_turn[action.key.split(':', 1)[1]] = self.sent_total

    def choose(self, world: WorldView) -> Decision | None:
        self.last_offer_review = []
        if world.tick != self.tick:
            self.tick = world.tick
            self.attempted.clear()
            self.sent_this_tick = 0
        rules = world.rules
        used = max(self.sent_total, len(world.request_results))
        if (not world.is_running() or world.self.health == 0 or world.self.failed_once
                or world.tick < self.retry_tick
                or self.sent_this_tick >= rules.new_commands_per_station_per_tick
                or used >= rules.max_request_records_per_station):
            return None
        remaining = max(0, rules.duration_ticks - world.tick)
        if not remaining:
            return None
        upkeep = world.self.upkeep_per_tick
        specialty = world.self.specialty
        imported = frozenset(RESOURCES) - {specialty}
        reserve = scaled(upkeep, min(self.policy.reserve_ticks, remaining))
        target = scaled(upkeep, min(self.policy.target_ticks, remaining))
        outgoing = [o for o in world.open_offers_from_me() if not o.is_expired_by(world.tick)]
        committed = Bundle.zero()
        for offer in outgoing:
            committed += offer.give
        available = world.self.inventory.saturating_subtract(committed)
        common = dict(run_id=world.run_id, request_id=generate_request_id())

        def decision(key: str, reason: str, message: pb.ClientMessage) -> Decision | None:
            if key in self.attempted or len(codec.encode_client_message(message)) > rules.max_command_bytes:
                return None
            return Decision(key, reason, message)

        if not world.self.inventory.saturating_subtract(reserve).covers(committed):
            for offer in outgoing:
                action = decision('withdraw:' + offer.offer_id, 'release stock needed for upkeep',
                                  codec.build_withdraw(**common, object_id=offer.offer_id))
                if action:
                    return action
            return None

        # Help the shortest supply first, rather than accepting tiny gifts
        # ahead of a larger trade that would prevent a shortage.
        def urgency(offer: Offer) -> float:
            return min((quantity(available, r) / max(1, quantity(upkeep, r))
                        for r in RESOURCES if quantity(offer.give, r) > quantity(offer.receive, r)),
                       default=float('inf'))

        for offer in sorted(world.open_offers_to_me(), key=lambda o: (urgency(o), o.created_tick, o.offer_id)):
            note = ''
            if offer.is_expired_by(world.tick):
                note = 'expired'
            elif not available.covers(offer.receive):
                note = 'insufficient stock to cover what it asks'
            else:
                after = available.minus(offer.receive) + offer.give
                safe = all(quantity(after, r) >= min(quantity(available, r), quantity(reserve, r))
                           for r in RESOURCES)
                fair = sum(quantity(offer.give, r) for r in RESOURCES) >= sum(quantity(offer.receive, r) for r in RESOURCES)
                specialty_payment = all(r == specialty or quantity(offer.receive, r) == 0 for r in RESOURCES)
                useful = any(r in imported and quantity(offer.give, r) > quantity(offer.receive, r)
                             and quantity(after, r) <= 2 * quantity(target, r) for r in RESOURCES)
                if not safe:
                    note = 'would breach the upkeep reserve'
                elif not offer.is_gift() and not specialty_payment:
                    note = 'would require payment beyond our specialty'
                elif not fair:
                    note = 'unfavorable: would give more than we receive'
                elif not offer.is_gift() and not useful:
                    note = 'already have enough of the offered resource'
                else:
                    action = decision('accept:' + offer.offer_id, 'accept useful fair exchange or free gift',
                                      codec.build_accept(**common, offer_id=offer.offer_id))
                    if action:
                        self.last_offer_review.append((offer, 'accepted'))
                        return action
                    note = 'already attempted this tick, or message too large'
            self.last_offer_review.append((offer, note))

        ttl = min(5, rules.max_offer_ttl_ticks, remaining)
        specialty_buffer = quantity(upkeep, specialty) * min(self.policy.reserve_ticks + ttl, remaining)
        spendable = max(0, quantity(available, specialty) - specialty_buffer)
        selling = {specialty} if spendable else set()
        # Always announce the two resources we import. An advertisement is
        # a willingness to barter, not a claim that a neighbor is in distress.
        own_ad = next((a for a in world.advertisements if a.posted_by(world.self_station_id)
                       and a.is_active() and not a.is_expired_by(world.tick)), None)
        routine_ok = rules.max_request_records_per_station - used > 4
        if own_ad is None and routine_ok and (selling or imported):
            ad_ttl = min(10, rules.max_publication_ttl_ticks, remaining)
            if ad_ttl > 0:
                action = decision('advertise', 'advertise specialty for fair resource exchanges',
                    codec.build_advertise(**common, selling=sorted(selling, key=lambda r: r.value),
                                          seeking=sorted(imported, key=lambda r: r.value),
                                          expires_tick=world.tick + ad_ttl))
                if action:
                    return action
        if ttl <= 0 or len(outgoing) >= rules.max_open_outgoing_offers:
            return None
        busy = {o.recipient_id for o in outgoing}
        suppliers = [a for a in world.advertisements if a.station_id != world.self_station_id
                     and a.station_id not in busy and a.is_active() and not a.is_expired_by(world.tick)
                     and (not world.directory or a.station_id in world.directory)]
        # Prefer nearby entries in the roster to spread demand. If no nearby
        # station sells the needed resource, any advertised supplier qualifies.
        roster = sorted(set(world.directory) | {world.self_station_id}
                        | {a.station_id for a in suppliers})
        group = roster.index(world.self_station_id) // len(RESOURCES)
        suppliers.sort(key=lambda a: (roster.index(a.station_id) // len(RESOURCES) != group,
                                      self.partner_turn.get(a.station_id, -1), a.station_id))
        for need in sorted(imported, key=lambda r: (quantity(available, r) / max(1, quantity(upkeep, r)), r.value)):
            if quantity(available, need) > quantity(upkeep, need) * min(self.policy.refill_ticks, remaining):
                continue
            urgent = quantity(available, need) < quantity(reserve, need)
            if not routine_ok and not urgent:
                continue
            amount = min(quantity(target, need) - quantity(available, need),
                         max(0, quantity(available, specialty) - quantity(reserve, specialty)) if urgent else spendable)
            if amount <= 0:
                continue
            for supplier in suppliers:
                if need in supplier.selling and specialty in supplier.seeking:
                    action = decision('partner:' + supplier.station_id, 'refill shortest supply with a fair batch exchange',
                        codec.build_offer(**common, recipient_id=supplier.station_id,
                                          give=bundle_of(specialty, amount), receive=bundle_of(need, amount),
                                          expires_tick=world.tick + ttl))
                    if action:
                        return action
        return None


async def run_trading(session: ClientSession, run_log: RunLog | None = None, *,
                      conservative: bool = False, policy: TradingPolicy | None = None) -> WorldView:
    strategy = ConservativeTradingStrategy(policy) if conservative or policy is not None else SelfSufficientStrategy()
    strategy.sent_total = len(session.world.request_results)
    logger.info('trading started: policy=%s station=%s specialty=%s',
                type(strategy).__name__, session.world.self_station_id, session.world.self.specialty.value)
    if run_log:
        run_log.run_started(session.world, mode='trade')
    logged_tick = None
    while True:
        world = session.world
        if world.tick != logged_tick:
            logged_tick = world.tick
            logger.info("%s", format_status_report(world))
            if run_log:
                run_log.tick_snapshot(world, sent_total=strategy.sent_total)
                run_log.transactions_settled(world)
                run_log.offers_snapshot(world)
                run_log.refresh_html()
        if world.phase in (pb.PHASE_FINISHED, pb.PHASE_ABORTED) or world.self.health == 0 or world.self.failed_once:
            if run_log:
                run_log.run_ended(world, reason='phase finished or station failed')
            return world
        action = strategy.choose(world)
        review_line = format_offer_review(world, strategy.last_offer_review)
        if review_line:
            logger.info('%s', review_line)
        if run_log:
            for offer, note in strategy.last_offer_review:
                if note != 'accepted':
                    run_log.offer_passed(world, offer, reason=note)
        if action is None:
            # Waiting for a partner is normal; it has no five-second deadline.
            sequence = world.snapshot_sequence
            await session.wait_for(lambda w: w.snapshot_sequence > sequence, timeout=None)
            continue
        strategy.record(action)
        logger.info('trading: %s (%s)', action.reason, format_sent_terms(action.message))
        if run_log:
            run_log.decision(world, key=action.key, reason=action.reason,
                              request_id=action.request_id, message=action.message)
        try:
            outcome = await asyncio.wait_for(session.send(request_id=action.request_id, message=action.message), timeout=15)
            if outcome.ok:
                logger.info('  -> ok%s', format_result_extra(outcome))
            else:
                retry_note = f' (retry after tick {outcome.retry_after_tick})' if outcome.retry_after_tick else ''
                logger.info('  -> rejected: %s%s', humanize_result_code(outcome.code), retry_note)
            if run_log:
                run_log.command_result(world, key=action.key, outcome=outcome)
            if outcome.code == pb.RESULT_CODE_RATE_LIMITED:
                strategy.retry_tick = max(world.tick + 1, outcome.retry_after_tick or 0)
            # Reconcile server inventory/commitments before every next decision.
            # Never blindly resend a command whose settlement is uncertain.
            await session.sync(timeout=15)
        except NotRunningError:
            # A final snapshot can arrive between choosing and sending.
            if session.world.phase not in (pb.PHASE_FINISHED, pb.PHASE_ABORTED):
                raise
        except asyncio.TimeoutError as exc:
            if run_log:
                run_log.run_ended(session.world, reason='command or sync timeout')
            raise RuntimeError('server did not acknowledge a command or synchronize within 15 seconds; stopped to avoid trading on stale inventory') from exc
