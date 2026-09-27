from dataclasses import replace
import asyncio

import pytest
import websockets

from fixtures import (make_state, make_self_observation, make_bundle, make_offer,
                      make_advertisement, make_readiness, make_result)
from planet_charlu import codec
from planet_charlu.config import ClientConfig, load_config
from planet_charlu.connection import BazaarConnection, REQUIRED_SUBPROTOCOL
from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.session import open_session, ClientSession
from planet_charlu.strategy import CooperativeStrategy, run_trading


def world(inventory=(30, 2, 5), offers=(), ads=(), specialty=pb.RESOURCE_WATER, tick=0):
    state = make_state(tick=tick, offers=list(offers), advertisements=list(ads),
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


def test_advertisement_uses_assigned_specialty_and_shortages():
    w = world(inventory=(1, 30, 2), specialty=pb.RESOURCE_FOOD)
    action = CooperativeStrategy().choose(w)
    body = action.message.advertise.body
    assert list(body.selling.items) == [pb.RESOURCE_FOOD]
    assert set(body.seeking.items) == {pb.RESOURCE_WATER, pb.RESOURCE_COMPONENTS}
    assert body.expires_tick <= w.tick + w.rules.max_publication_ttl_ticks


def test_trade_for_shortage_with_arbitrary_partner():
    w = world(ads=[make_advertisement(station_id='TEAM-Z')])
    strategy = CooperativeStrategy()
    skip_ad(strategy, w)
    body = strategy.choose(w).message.offer.body
    assert body.recipient_id == 'TEAM-Z'
    assert Bundle.from_wire(body.give) == Bundle(water=3)
    assert Bundle.from_wire(body.receive) == Bundle(food=3)
    assert body.expires_tick == 2


def test_accept_direction_and_reserve_protection():
    good = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 2), receive=make_bundle(2))
    action = CooperativeStrategy().choose(world(offers=[good]))
    assert action.message.accept.body.offer_id == good.offer_id
    expensive = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(0, 2), receive=make_bundle(29))
    assert CooperativeStrategy().choose(world(offers=[expensive])).message.WhichOneof('message') != 'accept'


def test_incoming_gift_accepted_even_with_shortage():
    gift = make_offer(proposer_id='P09', recipient_id='P01', receive=make_bundle())
    assert CooperativeStrategy().choose(world(inventory=(0, 0, 0), offers=[gift])).message.WhichOneof('message') == 'accept'


def test_small_help_request_paid_from_specialty_surplus():
    help_offer = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(), receive=make_bundle(2))
    assert CooperativeStrategy().choose(world(offers=[help_offer])).message.WhichOneof('message') == 'accept'
    assert CooperativeStrategy().choose(world(inventory=(3, 3, 3), offers=[help_offer])).message.WhichOneof('message') != 'accept'


def test_unaffordable_exchange_not_funded_by_promised_receipts():
    offer = make_offer(proposer_id='P09', recipient_id='P01', give=make_bundle(100, 2), receive=make_bundle(31))
    assert CooperativeStrategy().choose(world(offers=[offer])).message.WhichOneof('message') != 'accept'


def test_commitments_prevent_double_spending_and_unsafe_offers_withdrawn():
    outgoing = make_offer(give=make_bundle(28), receive=make_bundle(0, 1))
    action = CooperativeStrategy().choose(world(offers=[outgoing]))
    assert action.message.withdraw.body.object_id == outgoing.offer_id
    outgoing.give.water = 25
    w = world(offers=[outgoing], ads=[make_advertisement(station_id='P03')])
    strategy = CooperativeStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None  # remaining water needed across offer lifetime


def test_expired_offers_and_advertisements_ignored():
    gift = make_offer(proposer_id='P09', recipient_id='P01', receive=make_bundle(), expires_tick=1)
    w = world(tick=1, offers=[gift], ads=[make_advertisement(expires_tick=1)])
    strategy = CooperativeStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None


def test_gifts_rotate_across_all_eight_partners():
    ads = [make_advertisement(station_id=f'P{i:02}', selling=[], expires_tick=100) for i in range(2, 10)]
    w = world(inventory=(100, 30, 30), ads=ads)
    strategy = CooperativeStrategy()
    skip_ad(strategy, w)
    recipients = []
    for tick in range(8):
        snapshot = replace(w, tick=tick)
        # Keep one matching active ad so this test isolates partner selection.
        own = make_advertisement(station_id='P01', selling=[1, 2, 3], seeking=[], expires_tick=30)
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
    assert CooperativeStrategy().choose(replace(world(), phase=phase)) is None


def test_rate_capacity_and_message_size_limits():
    w = world(ads=[make_advertisement()])
    w = replace(w, rules=replace(w.rules, new_commands_per_station_per_tick=1))
    strategy = CooperativeStrategy()
    skip_ad(strategy, w)
    assert strategy.choose(w) is None
    assert strategy.choose(replace(w, tick=1)) is not None
    assert CooperativeStrategy().choose(replace(w, rules=replace(w.rules, max_command_bytes=1))) is None
    assert CooperativeStrategy().choose(replace(w, rules=replace(w.rules, max_request_records_per_station=0))) is None


def test_short_ttl_open_offer_limit_and_failed_station():
    w = world(ads=[make_advertisement()])
    strategy = CooperativeStrategy()
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


async def test_nine_planet_websocket_simulation():
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
