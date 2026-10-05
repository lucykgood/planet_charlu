from dataclasses import replace
import asyncio

import pytest
import websockets

from fixtures import (make_state, make_self_observation, make_bundle, make_offer,
                      make_advertisement, make_transaction, make_readiness, make_result)
from planet_charlu import codec
from planet_charlu.config import ClientConfig, load_config
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.domain.offers import Offer
from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.session import open_session, ClientSession
from planet_charlu.logging_utils import TICK_SEPARATOR
from planet_charlu.strategy import ConservativeTradingStrategy, SelfSufficientStrategy, run_trading


def world(inventory=(30, 2, 5), offers=(), ads=(), transactions=(), specialty=pb.RESOURCE_WATER, tick=0):
    state = make_state(tick=tick, offers=list(offers), advertisements=list(ads),
        transactions=list(transactions),
        self_observation=make_self_observation(inventory=make_bundle(*inventory), specialty=specialty))
    state.rules.max_request_records_per_station = 1000
    return WorldView.from_state(state)


def skip_ad(strategy, snapshot):
    action = strategy.choose(snapshot)
    assert action.message.WhichOneof('message') == 'advertise'
    strategy.record(action)


def test_runtime_modes():
    assert load_config(['--token', 't'], {}).mode == 'trade'
    assert load_config(['--token', 't', '--mode', 'validation'], {}).mode == 'validation'


@pytest.mark.parametrize('inventory', [(30, 14, 30), (30, 30, 14), (14, 30, 30), (30, 15, 30)])
def test_conservative_withholds_gifts_until_all_resources_cover_reserve_and_lifetime(inventory):
    w = world(inventory=inventory, ads=[make_advertisement(selling=[])])
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None


def test_conservative_gifts_only_for_advertised_specialty_emergency():
    ads = [make_advertisement(station_id='P02', selling=[], seeking=[]),
           make_advertisement(station_id='P03', selling=[], seeking=[pb.RESOURCE_WATER])]
    w = world(inventory=(22, 20, 20), ads=ads)
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    body = strategy.choose(w).message.offer.body
    assert body.recipient_id == 'P03'
    assert body.give.water == 2
    assert Bundle.from_wire(body.receive).is_zero()
    assert w.self.inventory.minus(Bundle.from_wire(body.give)).covers(Bundle(20, 20, 20))


def test_conservative_seeks_fifteen_ticks_and_prioritizes_trade_over_gift():
    w = world(inventory=(40, 14, 30), ads=[make_advertisement()])
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    body = strategy.choose(w).message.offer.body
    assert Bundle.from_wire(body.receive) == Bundle(food=1)
    assert Bundle.from_wire(body.give) == Bundle(water=1)


def test_conservative_protects_fifteen_ticks_on_incoming_paid_trade():
    offer = make_offer(proposer_id='P02', recipient_id='P01',
                       give=make_bundle(0, 3), receive=make_bundle(3))
    assert ConservativeTradingStrategy().choose(world(inventory=(17, 10, 30), offers=[offer])).message.WhichOneof('message') != 'accept'
    assert ConservativeTradingStrategy().choose(world(inventory=(18, 10, 30), offers=[offer])).message.WhichOneof('message') == 'accept'


def test_conservative_emergency_can_spend_below_target_without_withdrawing_rescue():
    w = world(inventory=(17, 1, 30), ads=[make_advertisement()])
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    action = strategy.choose(w)
    body = action.message.offer.body
    assert body.give.water == 6
    strategy.record(action)
    pending = make_offer(recipient_id='P02', give=body.give, receive=body.receive, expires_tick=body.expires_tick)
    next_world = replace(w, tick=1, offers=(Offer.from_wire(pending),))
    next_action = strategy.choose(next_world)
    assert next_action is None or next_action.message.WhichOneof('message') != 'withdraw'


def test_conservative_counts_open_commitments_before_gifting():
    pending = make_offer(recipient_id='P03', give=make_bundle(5), receive=make_bundle(0, 5))
    w = world(inventory=(24, 30, 30), offers=[pending], ads=[make_advertisement(selling=[])])
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None


def test_conservative_replenishes_specialty_below_fifteen_ticks():
    ad = make_advertisement(selling=[pb.RESOURCE_WATER], seeking=[pb.RESOURCE_FOOD])
    w = world(inventory=(14, 30, 30), ads=[ad])
    strategy = ConservativeTradingStrategy()
    skip_ad(strategy, w)
    body = strategy.choose(w).message.offer.body
    assert Bundle.from_wire(body.give) == Bundle(food=1)
    assert Bundle.from_wire(body.receive) == Bundle(water=1)
    incoming = make_offer(proposer_id='P02', recipient_id='P01',
                          give=make_bundle(1), receive=make_bundle(0, 1))
    assert ConservativeTradingStrategy().choose(replace(w, offers=(Offer.from_wire(incoming),))).message.WhichOneof('message') == 'accept'


def test_conservative_uses_upkeep_rates_and_caps_target_at_end_of_run():
    w = world(inventory=(50, 29, 30))
    w = replace(w, self=replace(w.self, upkeep_per_tick=Bundle(1, 2, 1)))
    action = ConservativeTradingStrategy().choose(w)
    assert pb.RESOURCE_FOOD in action.message.advertise.body.seeking.items
    late = replace(w, tick=98, self=replace(w.self, inventory=Bundle(2, 4, 2)))
    assert ConservativeTradingStrategy().choose(late) is None


def test_advertisement_uses_assigned_specialty_and_shortages():
    w = world(inventory=(1, 30, 2), specialty=pb.RESOURCE_FOOD)
    action = SelfSufficientStrategy().choose(w)
    body = action.message.advertise.body
    assert list(body.selling.items) == [pb.RESOURCE_FOOD]
    assert set(body.seeking.items) == {pb.RESOURCE_WATER, pb.RESOURCE_COMPONENTS}
    assert body.expires_tick <= w.tick + w.rules.max_publication_ttl_ticks


def test_trade_for_shortage_with_arbitrary_partner():
    # Default inventory's food=2 is below the 3-tick reserve (critical), so
    # this also exercises the escalated recovery cap (6, funded from surplus)
    # rather than the routine cap (3, funded from spendable).
    w = world(ads=[make_advertisement(station_id='TEAM-Z')])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    body = strategy.choose(w).message.offer.body
    assert body.recipient_id == 'TEAM-Z'
    assert Bundle.from_wire(body.give) == Bundle(water=4)
    assert Bundle.from_wire(body.receive) == Bundle(food=4)
    assert body.expires_tick == 5


def test_critical_recovery_dips_into_offer_lifetime_buffer():
    # Same commitment as the offer-lifetime-buffer test below, but with the
    # default food=2 (critical). A routine trade would be blocked here
    # because spendable.water == 0 (all 5 available water is reserved for
    # the offer's own upkeep lifetime) -- but recovering a critical resource
    # is funded from surplus instead, which only protects the 3-tick
    # reserve, so the trade goes through anyway.
    outgoing = make_offer(recipient_id='P02', give=make_bundle(25), receive=make_bundle(0, 1))
    w = world(offers=[outgoing], ads=[make_advertisement(station_id='P03')])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    action = strategy.choose(w)
    assert action is not None and action.message.WhichOneof('message') == 'offer'
    body = action.message.offer.body
    assert body.recipient_id == 'P03'
    assert Bundle.from_wire(body.give) == Bundle(water=2)
    assert Bundle.from_wire(body.receive) == Bundle(food=2)


def test_accept_direction_and_reserve_protection():
    good = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 2), receive=make_bundle(2))
    action = SelfSufficientStrategy().choose(world(offers=[good]))
    assert action.message.accept.body.offer_id == good.offer_id
    expensive = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 2), receive=make_bundle(29))
    assert SelfSufficientStrategy().choose(world(offers=[expensive])).message.WhichOneof('message') != 'accept'


def test_incoming_gift_accepted_even_with_shortage():
    gift = make_offer(proposer_id='P09', recipient_id='P01', receive=make_bundle())
    assert SelfSufficientStrategy().choose(world(inventory=(0, 0, 0), offers=[gift])).message.WhichOneof('message') == 'accept'


def test_free_gift_of_unproduced_resource_always_accepted():
    # A gift costs nothing, regardless of resource type or whether the giver
    # is a known/confirmed supplier -- refusing free stock of something we
    # can't produce ourselves would be pure self-sabotage.
    gift = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 0, 4), receive=make_bundle())
    assert SelfSufficientStrategy().choose(world(offers=[gift])).message.WhichOneof('message') == 'accept'


def test_worse_than_equal_trade_rejected():
    # 2 food for 3 water is affordable and would even count as "useful" by
    # resource type, but it is strictly worse than 1:1 for us -- refused.
    worse = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 2), receive=make_bundle(3))
    strategy = SelfSufficientStrategy()
    assert strategy.choose(world(offers=[worse])).message.WhichOneof('message') != 'accept'
    ((reviewed_offer, reason),) = strategy.last_offer_review
    assert reviewed_offer.offer_id == worse.offer_id
    assert reason == 'unfavorable: would give more than we receive'


def test_unfavorable_help_request_no_longer_fulfilled():
    # A small charity ask -- they give us nothing, but want 2 of our
    # specialty in return -- is a request to give away more than we get.
    # The "favorable or equal only" rule refuses it even though it's a tiny,
    # affordable amount.
    ask = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(), receive=make_bundle(2))
    strategy = SelfSufficientStrategy()
    assert strategy.choose(world(offers=[ask])).message.WhichOneof('message') != 'accept'
    assert strategy.last_offer_review[0][1] == 'unfavorable: would give more than we receive'


def test_incoming_trade_rejected_if_it_would_cost_an_unproduced_resource():
    # Plenty of components for a little food looks favorable by the
    # numbers, but it asks us to pay in food -- one of the two resources we
    # rely on trade for ourselves. Refused regardless of how good the ratio is.
    # (High food inventory here isolates this from the reserve-safety check,
    # which would otherwise trigger first on the smaller default balance.)
    offer = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 0, 5), receive=make_bundle(0, 1))
    strategy = SelfSufficientStrategy()
    assert strategy.choose(world(inventory=(30, 10, 5), offers=[offer])).message.WhichOneof('message') != 'accept'
    assert strategy.last_offer_review[0][1] == 'would require payment beyond our specialty'


def test_unaffordable_exchange_not_funded_by_promised_receipts():
    offer = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(100, 2), receive=make_bundle(31))
    strategy = SelfSufficientStrategy()
    assert strategy.choose(world(offers=[offer])).message.WhichOneof('message') != 'accept'
    assert strategy.last_offer_review[0][1] == 'insufficient stock to cover what it asks'


def test_commitments_prevent_double_spending_and_unsafe_offers_withdrawn():
    outgoing = make_offer(give=make_bundle(28), receive=make_bundle(0, 1))
    action = SelfSufficientStrategy().choose(world(offers=[outgoing]))
    assert action.message.withdraw.body.object_id == outgoing.offer_id
    outgoing.give.water = 25
    # food=4 keeps food out of the critical zone (>= the 3-tick reserve of
    # 3), isolating the offer-lifetime spendable buffer from the
    # critical-recovery override tested above.
    w = world(inventory=(30, 4, 5), offers=[outgoing], ads=[make_advertisement(station_id='P03')])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None  # remaining water needed across offer lifetime


def test_never_offers_to_pay_with_unproduced_resource():
    # Huge food surplus, and an ad that wants exactly it -- but that ad is
    # seeking food paid for with food, not our specialty (water). We only
    # ever pay with our specialty, so no match forms even though we could
    # easily afford the trade.
    w = world(inventory=(30, 100, 5),
              ads=[make_advertisement(station_id='P03', selling=[pb.RESOURCE_COMPONENTS], seeking=[pb.RESOURCE_FOOD])])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None


def test_specialty_shortfall_funds_recovery_from_other_resources():
    # Water is both our specialty and, at inventory=1, below the 3-tick
    # reserve -- a production shortfall, not a routine need. The
    # self-sufficiency rule lifts as a survival exception: seek water too,
    # but fund it from food (never from water itself -- that would be
    # buying water with water, a net no-op).
    w = world(inventory=(1, 30, 30),
              ads=[make_advertisement(station_id='P03', selling=[pb.RESOURCE_WATER], seeking=[pb.RESOURCE_FOOD])])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    action = strategy.choose(w)
    assert action.message.WhichOneof('message') == 'offer'
    body = action.message.offer.body
    assert body.recipient_id == 'P03'
    assert Bundle.from_wire(body.give) == Bundle(food=5)
    assert Bundle.from_wire(body.receive) == Bundle(water=5)


def test_confirmed_supplier_gates_unconfirmed_ads_once_learned():
    ads = [make_advertisement(station_id='P02', selling=[pb.RESOURCE_FOOD], seeking=[pb.RESOURCE_WATER]),
           make_advertisement(station_id='P05', selling=[pb.RESOURCE_FOOD], seeking=[pb.RESOURCE_WATER])]
    # Before any confirmed supplier, either currently-advertising station is
    # a valid candidate -- an ad is all we have to go on yet. P02 sorts
    # first alphabetically (no partner history yet either), so it wins.
    w = world(ads=ads)
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w).message.offer.body.recipient_id == 'P02'

    # Once P05 has actually delivered food (a settled transaction -- proof,
    # not a claim), P02's equally active ad is no longer trusted for food.
    tx = make_transaction(transaction_id='tx-1', proposer_id='P05', recipient_id='P01',
                          give=make_bundle(0, 3), receive=make_bundle(0, 0, 1))
    w2 = world(ads=ads, transactions=[tx])
    strategy2 = SelfSufficientStrategy()
    skip_ad(strategy2, w2)
    assert strategy2.choose(w2).message.offer.body.recipient_id == 'P05'


def test_expired_offers_and_advertisements_ignored():
    gift = make_offer(proposer_id='P09', recipient_id='P01', receive=make_bundle(), expires_tick=1)
    w = world(tick=1, offers=[gift], ads=[make_advertisement(expires_tick=1)])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None
    assert strategy.last_offer_review == [(Offer.from_wire(gift), 'expired')]


def test_offer_review_records_reserve_breach_reason():
    # Accepting would require giving away more water than the reserve
    # allows, even though the food it offers would rescue a critical
    # shortage -- refused, and the review says why.
    unsafe = make_offer(offer_id='unsafe', proposer_id='P08', recipient_id='P01',
                         give=make_bundle(0, 5), receive=make_bundle(29))
    strategy = SelfSufficientStrategy()
    strategy.choose(world(inventory=(30, 0, 5), offers=[unsafe]))
    assert strategy.last_offer_review == [(Offer.from_wire(unsafe), 'would breach the upkeep reserve')]


def test_offer_review_records_accepted_reason_for_the_chosen_offer():
    gift = make_offer(proposer_id='P09', recipient_id='P01', receive=make_bundle())
    strategy = SelfSufficientStrategy()
    action = strategy.choose(world(inventory=(0, 0, 0), offers=[gift]))
    assert action.message.WhichOneof('message') == 'accept'
    assert strategy.last_offer_review == [(Offer.from_wire(gift), 'accepted')]


def test_gifts_rotate_across_all_eight_partners():
    ads = [make_advertisement(station_id=f'P{i:02}', selling=[], expires_tick=100) for i in range(2, 10)]
    w = world(inventory=(100, 30, 30), ads=ads)
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    recipients = []
    for tick in range(8):
        snapshot = replace(w, tick=tick)
        # Keep one matching active ad so this test isolates partner selection.
        # (selling=[WATER] only, seeking=[] -- matching what this strategy
        # actually advertises: specialty surplus, no unmet needs here.)
        own = make_advertisement(station_id='P01', selling=[pb.RESOURCE_WATER], seeking=[], expires_tick=30)
        from planet_charlu.domain.advertisements import Advertisement
        snapshot = replace(snapshot, advertisements=w.advertisements + (Advertisement.from_wire(own),))
        action = strategy.choose(snapshot)
        assert action.message.WhichOneof('message') == 'offer'
        assert Bundle.from_wire(action.message.offer.body.receive).is_zero()
        assert action.message.offer.body.give.water <= 2
        recipients.append(action.message.offer.body.recipient_id)
        strategy.record(action)
    assert len(set(recipients)) == 8


@pytest.mark.parametrize('phase', [pb.PHASE_READY, pb.PHASE_PAUSED, pb.PHASE_FINISHED, pb.PHASE_ABORTED])
def test_no_commands_outside_running(phase):
    assert SelfSufficientStrategy().choose(replace(world(), phase=phase)) is None


def test_rate_capacity_and_message_size_limits():
    w = world(ads=[make_advertisement()])
    w = replace(w, rules=replace(w.rules, new_commands_per_station_per_tick=1))
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None
    assert strategy.choose(replace(w, tick=1)) is not None
    assert SelfSufficientStrategy().choose(replace(w, rules=replace(w.rules, max_command_bytes=1))) is None
    assert SelfSufficientStrategy().choose(replace(w, rules=replace(w.rules, max_request_records_per_station=0))) is None


def test_routine_advertising_suppressed_once_budget_reserve_reached():
    # max_request_records_per_station is a lifetime cap, not a per-tick one.
    # Abundant inventory means nothing is critical or even needed -- but it
    # would still advertise its specialty surplus, if not for the budget.
    w = world(inventory=(30, 30, 30))
    w = replace(w, rules=replace(w.rules, max_request_records_per_station=10))
    assert SelfSufficientStrategy().choose(w).message.WhichOneof('message') == 'advertise'
    strategy = SelfSufficientStrategy()
    strategy.sent_total = 10 - SelfSufficientStrategy.EMERGENCY_RESERVE  # only the reserve is left
    assert strategy.choose(w) is None


def test_non_critical_seeking_suppressed_once_budget_reserve_reached():
    # components=5 is below the 6-tick target (a routine need) but not the
    # 3-tick reserve (not critical). With only the emergency reserve left,
    # seeking it is rationed even though a matching ad exists.
    w = world(inventory=(30, 30, 5),
              ads=[make_advertisement(station_id='P04', selling=[pb.RESOURCE_COMPONENTS], seeking=[pb.RESOURCE_WATER])])
    w = replace(w, rules=replace(w.rules, max_request_records_per_station=10))
    strategy = SelfSufficientStrategy()
    strategy.sent_total = 10 - SelfSufficientStrategy.EMERGENCY_RESERVE
    assert strategy.choose(w) is None


def test_critical_recovery_not_rationed_by_emergency_reserve():
    # Default food=2 is critical. Even with only the emergency reserve left
    # of the lifetime budget, recovering a critical resource is never
    # rationed -- unlike the routine cases above.
    w = world(ads=[make_advertisement(station_id='P03')])
    w = replace(w, rules=replace(w.rules, max_request_records_per_station=10))
    strategy = SelfSufficientStrategy()
    strategy.sent_total = 10 - SelfSufficientStrategy.EMERGENCY_RESERVE
    action = strategy.choose(w)
    assert action is not None and action.message.WhichOneof('message') == 'offer'
    assert action.message.offer.body.recipient_id == 'P03'


def test_short_ttl_open_offer_limit_and_failed_station():
    w = world(ads=[make_advertisement()])
    strategy = SelfSufficientStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(replace(w, rules=replace(w.rules, max_open_outgoing_offers=0))) is None
    action = strategy.choose(replace(w, rules=replace(w.rules, max_offer_ttl_ticks=1)))
    assert action.message.offer.body.expires_tick == 1
    assert strategy.choose(replace(w, self=replace(w.self, failed_once=True))) is None


async def test_disconnect_wakes_snapshot_waiter():
    async def closed():
        if False:
            yield
    session = ClientSession(None, closed(), world())
    session.start_pump()
    try:
        with pytest.raises(ConnectionError, match='closed'):
            await asyncio.wait_for(session.wait_for(lambda w: False, timeout=None), 1)
    finally:
        await session.stop_pump()


async def test_nine_planet_websocket_simulation(caplog):
    """Real handshake/codec/session/runner with eight accepting peer planets."""
    peers = {f'P{i:02}' for i in range(2, 10)}
    recipients = set()
    kinds = []
    errors = []

    async def handler(socket):
        try:
            inventory = [100, 30, 30]
            offers = [make_offer(offer_id='gift', proposer_id='P09', recipient_id='P01',
                                 give=make_bundle(0, 1), receive=make_bundle(), expires_tick=100)]
            ads = [make_advertisement(station_id=p, selling=[], expires_tick=100) for p in sorted(peers)]
            sequence = 0
            tick = 0
            phase = pb.PHASE_READY

            async def send_state():
                nonlocal sequence
                sequence += 1
                state = make_state(snapshot_sequence=sequence, world_version=sequence, tick=tick,
                    phase=phase, offers=offers, advertisements=ads,
                    self_observation=make_self_observation(inventory=make_bundle(*inventory)))
                state.rules.max_request_records_per_station = 100
                for planet in sorted(peers | {'P01'}):
                    state.directory.items.add(station_id=planet, display_name=planet)
                message = pb.ServerMessage()
                message.state.CopyFrom(state)
                await socket.send(message.SerializeToString())

            await send_state()
            ready = codec.decode_client_message(await socket.recv())
            assert ready.WhichOneof('message') == 'ready'
            response = pb.ServerMessage()
            response.readiness.CopyFrom(make_readiness())
            await socket.send(response.SerializeToString())
            phase = pb.PHASE_RUNNING
            await send_state()
            async for raw in socket:
                message = codec.decode_client_message(raw)
                kind = message.WhichOneof('message')
                kinds.append(kind)
                if kind == 'sync':
                    tick += 1
                    if recipients == peers:
                        phase = pb.PHASE_FINISHED
                    await send_state()
                    if phase == pb.PHASE_FINISHED:
                        return
                    continue
                command = getattr(message, kind)
                assert phase == pb.PHASE_RUNNING
                if kind == 'accept':
                    assert command.body.offer_id == 'gift'
                    offers[0].status = pb.OFFER_STATUS_ACCEPTED
                    inventory[1] += 1
                elif kind == 'advertise':
                    ads[:] = [ad for ad in ads if ad.station_id != 'P01']
                    ads.append(make_advertisement(station_id='P01', selling=list(command.body.selling.items),
                        seeking=list(command.body.seeking.items), expires_tick=command.body.expires_tick))
                elif kind == 'offer':
                    body = command.body
                    assert body.recipient_id in peers
                    assert body.expires_tick > tick
                    assert body.give.water <= inventory[0] - 3
                    inventory[0] -= body.give.water
                    recipients.add(body.recipient_id)
                    offers.append(make_offer(offer_id=command.request_id, recipient_id=body.recipient_id,
                        give=body.give, receive=body.receive, status=pb.OFFER_STATUS_ACCEPTED))
                else:
                    raise AssertionError(kind)
                response = pb.ServerMessage()
                response.result.CopyFrom(make_result(request_id=command.request_id))
                await socket.send(response.SerializeToString())
        except Exception as exc:
            errors.append(exc)
            raise

    with caplog.at_level('INFO'):
        async with websockets.serve(handler, '127.0.0.1', 0, subprotocols=[REQUIRED_SUBPROTOCOL]) as server:
            port = server.sockets[0].getsockname()[1]
            async with BazaarConnection(ClientConfig(f'ws://127.0.0.1:{port}', 'test', 'P01')) as connection:
                session = await open_session(connection)
                try:
                    final = await asyncio.wait_for(run_trading(session), 3)
                finally:
                    await session.stop_pump()
    assert not errors
    assert recipients == peers
    assert {'accept', 'advertise', 'offer', 'sync'} <= set(kinds)
    assert final.phase == pb.PHASE_FINISHED
    assert final.self.inventory == Bundle(84, 31, 30)

    # The human-readable console output stays readable: a per-tick dashboard
    # and short ok/rejected result lines, not raw request IDs on every command.
    messages = [record.getMessage() for record in caplog.records]
    assert any(m.startswith(TICK_SEPARATOR + '\ntick 0 | ') for m in messages)
    assert '  -> ok' in messages
    assert not any('request_id=' in m and m.startswith('trading:') for m in messages)
