from dataclasses import replace

import pytest

from fixtures import make_advertisement, make_bundle, make_offer, make_self_observation, make_state
from planet_charlu.domain.world import WorldView
from planet_charlu.domain.resources import Bundle
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.strategy import ConservativeTradingStrategy, TradingPolicy


def world(inventory=(200, 24, 120), offers=(), ads=(), tick=0):
    state = make_state(tick=tick, offers=list(offers), advertisements=list(ads),
                       self_observation=make_self_observation(inventory=make_bundle(*inventory),
                                                            upkeep_per_tick=make_bundle(4, 4, 4)))
    state.rules.max_request_records_per_station = 1000
    return WorldView.from_state(state)


def after_ad(strategy, snapshot):
    ad = strategy.choose(snapshot)
    assert ad.message.WhichOneof('message') == 'advertise'
    strategy.record(ad)
    return strategy.choose(snapshot)


def test_policy_api_and_validation():
    policy = TradingPolicy(reserve_ticks=2, refill_ticks=5, target_ticks=12)
    assert ConservativeTradingStrategy(policy).policy == policy
    with pytest.raises(ValueError):
        TradingPolicy(reserve_ticks=6, refill_ticks=3)
    with pytest.raises(ValueError):
        TradingPolicy(reserve_ticks=1.5)


def test_refill_in_upkeep_scaled_equal_batches():
    snapshot = world(ads=[make_advertisement()])
    action = after_ad(ConservativeTradingStrategy(), snapshot)
    assert Bundle.from_wire(action.message.offer.body.give) == Bundle(water=36)
    assert Bundle.from_wire(action.message.offer.body.receive) == Bundle(food=36)


def test_no_one_tick_topups_or_unsolicited_gifts():
    snapshot = world(inventory=(200, 56, 120), ads=[make_advertisement()])
    assert after_ad(ConservativeTradingStrategy(), snapshot) is None


def test_advertise_imports_even_when_supplied_and_only_renew_after_expiry():
    own = make_advertisement(station_id='P01', selling=[pb.RESOURCE_WATER],
                            seeking=[pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS], expires_tick=3)
    snapshot = world(inventory=(200, 120, 120), ads=[own], tick=2)
    strategy = ConservativeTradingStrategy()
    assert strategy.choose(snapshot) is None
    action = strategy.choose(replace(snapshot, tick=3))
    assert action.message.WhichOneof('message') == 'advertise'
    assert set(action.message.advertise.body.seeking.items) == {pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS}


def test_help_neighbor_with_fair_trade_above_own_target_but_limit_accumulation():
    offer = make_offer(proposer_id='P02', recipient_id='P01',
                       give=make_bundle(0, 36), receive=make_bundle(36))
    strategy = ConservativeTradingStrategy()
    assert strategy.choose(world(inventory=(200, 60, 120), offers=[offer])).message.WhichOneof('message') == 'accept'
    assert strategy.choose(world(inventory=(200, 100, 120), offers=[offer])).message.WhichOneof('message') != 'accept'


@pytest.mark.parametrize('inventory,payment', [((40, 0, 120), 36), ((200, 0, 120), 37)])
def test_reject_reserve_breach_or_unfair_price(inventory, payment):
    offer = make_offer(give=make_bundle(0, 36), receive=make_bundle(payment))
    assert ConservativeTradingStrategy().choose(world(inventory=inventory, offers=[offer])).message.WhichOneof('message') != 'accept'


def test_commitments_protect_stock_and_unsafe_offer_is_withdrawn():
    offer = make_offer(give=make_bundle(192), receive=make_bundle(0, 192))
    action = ConservativeTradingStrategy().choose(world(offers=[offer]))
    assert action.message.WhichOneof('message') == 'withdraw'


def test_expired_gift_ignored_and_free_gift_accepted_during_shortage():
    gift = make_offer(proposer_id='P02', recipient_id='P01',
                      give=make_bundle(0, 8), receive=make_bundle(), expires_tick=2)
    strategy = ConservativeTradingStrategy()
    assert strategy.choose(world(inventory=(0, 0, 0), offers=[gift])).message.WhichOneof('message') == 'accept'
    assert strategy.choose(world(inventory=(0, 0, 0), offers=[gift], tick=2)).message.WhichOneof('message') != 'accept'


def test_rate_and_request_budget_limits_are_respected():
    snapshot = world(ads=[make_advertisement()])
    snapshot = replace(snapshot, rules=replace(snapshot.rules, new_commands_per_station_per_tick=1))
    strategy = ConservativeTradingStrategy()
    assert after_ad(strategy, snapshot) is None
    from planet_charlu.domain.advertisements import Advertisement
    own = make_advertisement(station_id='P01', selling=[pb.RESOURCE_WATER],
                            seeking=[pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS], expires_tick=3)
    updated = replace(snapshot, tick=1,
                      advertisements=snapshot.advertisements + (Advertisement.from_wire(own),))
    assert strategy.choose(updated).message.WhichOneof('message') == 'offer'
    strategy.sent_total = snapshot.rules.max_request_records_per_station
    assert strategy.choose(replace(snapshot, tick=2)) is None


def test_end_of_run_target_prevents_unneeded_proposal():
    snapshot = world(inventory=(200, 8, 8), tick=98, ads=[make_advertisement()])
    assert after_ad(ConservativeTradingStrategy(), snapshot) is None


def test_supplier_outside_preferred_roster_group_remains_eligible():
    snapshot = world(ads=[make_advertisement(station_id='P08')])
    snapshot = replace(snapshot, directory=tuple(f'P{i:02d}' for i in range(1, 10)))
    action = after_ad(ConservativeTradingStrategy(), snapshot)
    assert action.message.offer.body.recipient_id == 'P08'


@pytest.mark.parametrize('phase', [pb.PHASE_READY, pb.PHASE_PAUSED, pb.PHASE_FINISHED, pb.PHASE_ABORTED])
def test_no_conservative_commands_outside_running(phase):
    assert ConservativeTradingStrategy().choose(replace(world(), phase=phase)) is None


def test_no_commands_after_permanent_failure_or_before_retry_tick():
    snapshot = world()
    assert ConservativeTradingStrategy().choose(replace(snapshot, self=replace(snapshot.self, failed_once=True))) is None
    strategy = ConservativeTradingStrategy()
    strategy.retry_tick = 5
    assert strategy.choose(snapshot) is None
