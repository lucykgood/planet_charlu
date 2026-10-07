import asyncio
from dataclasses import replace

import pytest

from fixtures import (make_advertisement, make_bundle, make_offer, make_result,
                      make_self_observation, make_state, make_transaction)
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.session import ClientSession
from planet_charlu.strategy import SimplifiedTradingStrategy, run_trading


def world(inventory=(50, 14, 30), *, offers=(), ads=(), tick=0, upkeep=(1, 1, 1)):
    wire = make_state(tick=tick, offers=offers, advertisements=ads,
        self_observation=make_self_observation(inventory=make_bundle(*inventory),
                                               upkeep_per_tick=make_bundle(*upkeep)))
    wire.rules.duration_ticks = 120
    wire.rules.max_request_records_per_station = 2048
    wire.rules.new_commands_per_station_per_tick = 1
    wire.rules.max_offer_ttl_ticks = 5
    return WorldView.from_state(wire)


def food_ad(station_id='P02'):
    return make_advertisement(station_id=station_id, expires_tick=120)


def test_refills_before_advertising_or_gifting():
    w = world(ads=[food_ad()])
    action = SimplifiedTradingStrategy().choose(w)
    body = action.message.offer.body
    assert body.give.water == body.receive.food == 11
    assert body.expires_tick == 5


def test_reorder_threshold_and_nonunit_upkeep():
    w = world(inventory=(70, 29, 30), upkeep=(1, 2, 1), ads=[food_ad()])
    assert SimplifiedTradingStrategy().choose(w).message.offer.body.receive.food == 21
    enough = world(inventory=(70, 30, 30), upkeep=(1, 2, 1), ads=[food_ad()])
    assert SimplifiedTradingStrategy().choose(enough).message.WhichOneof('message') == 'advertise'


def test_pending_purchase_blocks_duplicates_without_supplying_upkeep():
    pending = make_offer(give=make_bundle(11), receive=make_bundle(0, 11), expires_tick=5)
    w = world(offers=[pending], ads=[food_ad(), food_ad('P03')])
    action = SimplifiedTradingStrategy().choose(w)
    assert action.message.WhichOneof('message') == 'advertise'
    assert list(action.message.advertise.body.seeking.items) == [pb.RESOURCE_FOOD]


def test_expired_supplier_does_not_block_replacement():
    strategy = SimplifiedTradingStrategy()
    strategy.partner_turn['P02'] = 1
    strategy.known_suppliers = {'P02': {Resource.FOOD}}
    expired = make_offer(give=make_bundle(11), receive=make_bundle(0, 11), expires_tick=5)
    w = world(tick=5, offers=[expired], ads=[food_ad(), food_ad('P03')])
    assert strategy.choose(w).message.offer.body.recipient_id == 'P03'


def test_payments_protect_floor_through_expiry_and_subtract_commitments():
    pending = make_offer(recipient_id='P03', give=make_bundle(10), receive=make_bundle(0, 0, 10), expires_tick=5)
    w = world(inventory=(25, 14, 14), offers=[pending], ads=[food_ad()])
    body = SimplifiedTradingStrategy().choose(w).message.offer.body
    assert body.give.water == 5
    assert w.self.inventory.water - pending.give.water - body.give.water == 10


def test_unsafe_commitments_are_withdrawn():
    pending = make_offer(give=make_bundle(11), receive=make_bundle(0, 11), expires_tick=5)
    action = SimplifiedTradingStrategy().choose(world(inventory=(20, 14, 30), offers=[pending]))
    assert action.message.withdraw.body.object_id == pending.offer_id


def test_incoming_gift_is_accepted_even_when_empty():
    gift = make_offer(proposer_id='P02', recipient_id='P01', give=make_bundle(0, 3), receive=make_bundle())
    assert SimplifiedTradingStrategy().choose(world(inventory=(0, 0, 0), offers=[gift])).message.WhichOneof('message') == 'accept'


@pytest.mark.parametrize('water,food_cost,accepted', [(8, 3, True), (7, 3, False), (30, 4, False)])
def test_incoming_payment_floor_and_favorable_terms(water, food_cost, accepted):
    offer = make_offer(proposer_id='P02', recipient_id='P01', give=make_bundle(0, 3), receive=make_bundle(food_cost))
    action = SimplifiedTradingStrategy().choose(world(inventory=(water, 14, 30), offers=[offer]))
    assert (action is not None and action.message.WhichOneof('message') == 'accept') is accepted


def test_lowest_coverage_is_purchased_first():
    ads = [food_ad(), make_advertisement(station_id='P03', selling=[pb.RESOURCE_COMPONENTS], expires_tick=120)]
    body = SimplifiedTradingStrategy().choose(world(inventory=(60, 14, 8), ads=ads)).message.offer.body
    assert body.recipient_id == 'P03'
    assert body.receive.components == 17


def test_specialty_replenishment_uses_only_other_stock_above_target():
    ad = make_advertisement(selling=[pb.RESOURCE_WATER], seeking=[pb.RESOURCE_FOOD], expires_tick=120)
    body = SimplifiedTradingStrategy().choose(world(inventory=(14, 30, 30), ads=[ad])).message.offer.body
    assert body.give.food == body.receive.water == 5


@pytest.mark.parametrize('inventory,expected', [((27, 25, 25), 2), ((26, 25, 25), 1),
                                                ((25, 25, 25), 0), ((50, 24, 30), 0)])
def test_gifts_keep_every_resource_at_target(inventory, expected):
    w = world(inventory=inventory, ads=[make_advertisement(selling=[], expires_tick=120)])
    w = replace(w, rules=replace(w.rules, new_commands_per_station_per_tick=5))
    strategy = SimplifiedTradingStrategy()
    strategy.record(strategy.choose(w))  # Publish before sharing.
    action = strategy.choose(w)
    if expected:
        assert action.message.offer.body.give.water == expected
        assert Bundle.from_wire(action.message.offer.body.receive).is_zero()
    else:
        assert action is None


def test_gifts_account_for_existing_promises():
    pending = make_offer(give=make_bundle(10), receive=make_bundle(0, 10), expires_tick=5)
    w = world(inventory=(35, 30, 30), offers=[pending],
              ads=[make_advertisement(station_id='P03', selling=[], expires_tick=120)])
    w = replace(w, rules=replace(w.rules, new_commands_per_station_per_tick=5))
    strategy = SimplifiedTradingStrategy()
    strategy.record(strategy.choose(w))
    assert strategy.choose(w) is None


def test_end_of_run_caps_targets_and_zero_upkeep_never_needs_stock():
    w = world(tick=118, inventory=(2, 2, 0), upkeep=(1, 1, 0))
    assert SimplifiedTradingStrategy().choose(w) is None


def test_server_limits_phase_failure_and_backoff():
    w = world(ads=[food_ad()])
    strategy = SimplifiedTradingStrategy()
    strategy.record(strategy.choose(w))
    assert strategy.choose(w) is None
    assert SimplifiedTradingStrategy().choose(replace(w, phase=pb.PHASE_READY)) is None
    assert SimplifiedTradingStrategy().choose(replace(w, self=replace(w.self, failed_once=True))) is None
    assert SimplifiedTradingStrategy().choose(replace(w, rules=replace(w.rules, max_request_records_per_station=0))) is None
    assert SimplifiedTradingStrategy().choose(replace(w, rules=replace(w.rules, max_command_bytes=1))) is None
    strategy = SimplifiedTradingStrategy()
    strategy.retry_tick = 5
    assert strategy.choose(w) is None
    limited = replace(w, rules=replace(w.rules, max_open_outgoing_offers=0))
    assert strategy.choose(replace(limited, tick=5)).message.WhichOneof('message') == 'advertise'


@pytest.mark.parametrize('delay', [1, 3])
async def test_runner_survives_120_one_second_ticks_without_incoming_gifts(delay, caplog):
    """Virtual one-second ticks, finite supplier stock, and delayed settlement.

    Wall time is compressed; expiry, upkeep, production, and command limits
    all use simulation ticks. One food supplier never responds. Both legacy
    flags are set to also check simplified policy selection takes precedence.
    """
    queue = asyncio.Queue()
    inventory = [30, 30, 30]
    peer_stock = {'P02': 30, 'P03': 30}
    offers, transactions, results = [], [], []
    due = {}
    ads = [food_ad('P00'), food_ad(), make_advertisement(
        station_id='P03', selling=[pb.RESOURCE_COMPONENTS], expires_tick=120)]
    tick, version, sequence, health = 0, 1, 1, 100
    max_open = 0
    sent_ticks = set()

    def state():
        wire = make_state(tick=tick, snapshot_sequence=sequence, world_version=version,
            phase=pb.PHASE_FINISHED if tick == 120 else pb.PHASE_RUNNING,
            self_observation=make_self_observation(inventory=make_bundle(*inventory), health=health,
                last_production=make_bundle(5), failed_once=health <= 0),
            offers=offers, advertisements=ads, transactions=transactions, request_results=results)
        wire.rules.duration_ticks = 120
        wire.rules.tick_duration_ms = 1000
        wire.rules.new_commands_per_station_per_tick = 1
        wire.rules.max_offer_ttl_ticks = 5
        wire.rules.max_open_outgoing_offers = 3
        wire.rules.max_request_records_per_station = 2048
        return wire

    async def push():
        nonlocal sequence
        sequence += 1
        message = pb.ServerMessage()
        message.state.CopyFrom(state())
        await queue.put(message)

    class Broker:
        async def send(self, message):
            nonlocal version, max_open
            kind = message.WhichOneof('message')
            if kind == 'sync':
                await push()
                return
            assert tick not in sent_ticks
            sent_ticks.add(tick)
            command = getattr(message, kind)
            if kind == 'offer':
                body = command.body
                offer = make_offer(offer_id=command.request_id, recipient_id=body.recipient_id,
                    give=body.give, receive=body.receive, created_tick=tick, expires_tick=body.expires_tick)
                offers.append(offer)
                due[offer.offer_id] = tick + delay
                live = [o for o in offers if o.status == pb.OFFER_STATUS_OPEN and o.expires_tick > tick]
                max_open = max(max_open, len(live))
                assert sum(o.give.water for o in live) <= inventory[0]
                for resource in (pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS):
                    assert sum(getattr(o.receive, 'food' if resource == pb.RESOURCE_FOOD else 'components') > 0
                               for o in live) <= 1
            elif kind == 'advertise':
                ads[:] = [a for a in ads if a.station_id != 'P01']
                ads.append(make_advertisement(station_id='P01', selling=list(command.body.selling.items),
                    seeking=list(command.body.seeking.items), expires_tick=command.body.expires_tick))
            elif kind == 'withdraw':
                next(o for o in offers if o.offer_id == command.body.object_id).status = pb.OFFER_STATUS_WITHDRAWN
            else:
                raise AssertionError(kind)
            version += 1
            result = make_result(request_id=command.request_id, object_id=command.request_id)
            result.processed_version = version
            result.processed_tick = tick
            results.append(result)
            await push()
            response = pb.ServerMessage()
            response.result.CopyFrom(result)
            await queue.put(response)

    async def messages():
        while True:
            yield await queue.get()

    async def ticking():
        nonlocal tick, version, health
        for next_tick in range(1, 121):
            await asyncio.sleep(0.002)
            tick = next_tick
            inventory[0] += 5
            for peer in peer_stock:
                peer_stock[peer] += 2  # Production net of that peer's own specialty upkeep.
            for offer in offers:
                if offer.status != pb.OFFER_STATUS_OPEN:
                    continue
                if offer.expires_tick <= tick:
                    offer.status = pb.OFFER_STATUS_EXPIRED
                elif due[offer.offer_id] <= tick and offer.recipient_id in peer_stock:
                    give, receive = Bundle.from_wire(offer.give), Bundle.from_wire(offer.receive)
                    amount = receive.food or receive.components
                    assert peer_stock[offer.recipient_id] >= amount
                    peer_stock[offer.recipient_id] -= amount
                    after = Bundle(*inventory).minus(give) + receive
                    inventory[:] = [after.water, after.food, after.components]
                    offer.status = pb.OFFER_STATUS_ACCEPTED
                    transactions.append(make_transaction(transaction_id='tx-' + offer.offer_id,
                        offer_id=offer.offer_id, recipient_id=offer.recipient_id, give=offer.give,
                        receive=offer.receive, settled_tick=tick))
            for r in range(3):
                if inventory[r]:
                    inventory[r] -= 1
                else:
                    health -= 5
            version += 1
            await push()

    session = ClientSession(Broker(), messages(), WorldView.from_state(state()))
    session.start_pump()
    ticker = asyncio.create_task(ticking())
    try:
        with caplog.at_level('INFO'):
            final = await asyncio.wait_for(run_trading(session, simplified=True, conservative=True), timeout=5)
        assert final.tick == 120 and final.phase == pb.PHASE_FINISHED
        assert final.self.health == 100 and not final.self.failed_once
        assert sum(tx.receive.food for tx in transactions) >= 90
        assert sum(tx.receive.components for tx in transactions) >= 90
        assert any(o.status == pb.OFFER_STATUS_EXPIRED and o.recipient_id == 'P00' for o in offers)
        assert max_open <= 3
        assert any('policy=SimplifiedTradingStrategy' in r.getMessage() for r in caplog.records)
    finally:
        ticker.cancel()
        await asyncio.gather(ticker, return_exceptions=True)
        await session.stop_pump()
