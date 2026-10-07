import asyncio
import threading
import contextlib
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from fixtures import (make_advertisement, make_bundle, make_offer, make_result,
                      make_self_observation, make_state, make_transaction)
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.logging_utils import format_status_report
from planet_charlu.session import ClientSession
from planet_charlu.strategy import RestrainedTradingStrategy, BaseStrategy, run_trading
from planet_charlu.structured_log import LiveSummaryRenderer, RunLog


def snapshot(inventory=(200, 10, 50), tick_ms=1000, **kwargs):
    state = make_state(self_observation=make_self_observation(inventory=make_bundle(*inventory)), **kwargs)
    state.rules.tick_duration_ms = tick_ms
    state.rules.max_request_records_per_station = 1000
    return WorldView.from_state(state)


def outcome(version=5):
    wire = make_result()
    wire.processed_version = version
    return CommandOutcome.from_wire(wire)


def test_speed_change_increases_stock_targets_and_purchase_size():
    slow = snapshot(advertisements=[make_advertisement()])
    strategy = RestrainedTradingStrategy()
    # Consume the initial ad; food=10 is above the slow emergency threshold.
    strategy.record(strategy.choose(slow))
    slow_body = strategy.choose(slow).message.offer.body
    fast = replace(slow, rules=replace(slow.rules, tick_duration_ms=100))
    fast_body = strategy.choose(fast).message.offer.body
    assert fast_body.receive.food > slow_body.receive.food
    assert strategy.target_ticks(fast) > strategy.target_ticks(slow) >= 15
    assert strategy.reserve_ticks(fast) >= 15
    assert fast_body.expires_tick > slow_body.expires_tick
    assert fast.self.inventory.minus(Bundle.from_wire(fast_body.give)).water >= 3


def test_latency_measurements_raise_targets_even_without_speed_change():
    strategy = RestrainedTradingStrategy()
    w = snapshot()
    before = strategy.target_ticks(w)
    strategy.observe_latency(6.0)
    assert strategy.target_ticks(w) > before
    strategy.observe_latency(float('inf'))
    assert strategy.latency_ticks(w) == 6


def test_settlement_latency_includes_wait_for_partner(monkeypatch):
    strategy = RestrainedTradingStrategy()
    strategy.pending_trade_times['pending'] = 10.0
    monkeypatch.setattr('planet_charlu.strategy.time.monotonic', lambda: 16.0)
    w = snapshot(transactions=[make_transaction(offer_id='pending', give=make_bundle(4), receive=make_bundle(0, 4))])
    strategy.choose(w)
    assert strategy.latency_ticks(w) == 6
    assert not strategy.pending_trade_times


def test_expired_offer_is_removed_from_latency_tracking():
    strategy = RestrainedTradingStrategy()
    strategy.pending_trade_times['expired'] = 0.0
    strategy.choose(snapshot(tick=5, offers=[make_offer(offer_id='expired', expires_tick=5)]))
    assert not strategy.pending_trade_times


def test_urgent_purchase_uses_only_command_slot_before_ad_refresh():
    w = snapshot(inventory=(50, 1, 30), advertisements=[make_advertisement()])
    w = replace(w, rules=replace(w.rules, new_commands_per_station_per_tick=1))
    strategy = RestrainedTradingStrategy()
    action = strategy.choose(w)
    assert action.message.offer.body.receive.food > 0
    strategy.record(action)
    assert strategy.choose(w) is None


def test_larger_routine_batches_keep_reserve_and_offer_lifetime_stock():
    w = snapshot(inventory=(100, 5, 30), advertisements=[make_advertisement()])
    strategy = RestrainedTradingStrategy()
    strategy.record(strategy.choose(w))
    body = strategy.choose(w).message.offer.body
    assert body.receive.food == 8
    assert w.self.inventory.minus(Bundle.from_wire(body.give)).water >= 15 + body.expires_tick


def test_fast_gifting_is_withheld_and_end_of_run_caps_targets():
    w = snapshot(inventory=(100, 20, 20), tick_ms=100,
                 advertisements=[make_advertisement(selling=[])])
    strategy = RestrainedTradingStrategy()
    strategy.record(strategy.choose(w))
    assert strategy.choose(w) is None
    late = replace(w, tick=98, self=replace(w.self, inventory=Bundle(2, 2, 2)))
    assert RestrainedTradingStrategy().choose(late) is None


def test_status_report_shows_policy_reserve_and_earlier_urgency():
    report = format_status_report(snapshot(inventory=(30, 10, 18)), reserve_ticks=15, critical_ticks=11)
    assert 'food=10 (reserve 15, CRITICAL)' in report
    assert 'components=18 (reserve 15, ok)' in report


@pytest.mark.parametrize('version', [5, 6])
async def test_reconcile_reuses_snapshot_at_or_after_processed_version(version):
    session = ClientSession(None, None, snapshot(world_version=version))
    session.sync = AsyncMock()
    assert await session.reconcile(outcome()) is session.world
    session.sync.assert_not_awaited()


async def test_reconcile_reuses_snapshot_containing_request_record():
    session = ClientSession(None, None, snapshot(request_results=[make_result()]))
    session.sync = AsyncMock()
    assert await session.reconcile(outcome(version=0)) is session.world
    session.sync.assert_not_awaited()


async def test_reconcile_rejects_new_sequence_without_settled_version():
    session = ClientSession(None, None, snapshot(snapshot_sequence=100, world_version=4))
    settled = replace(session.world, world_version=5)
    session.sync = AsyncMock(return_value=settled)
    assert await session.reconcile(outcome()) is settled
    session.sync.assert_awaited_once()


async def test_reconcile_waits_if_sync_is_raced_by_an_older_tick_push():
    session = ClientSession(None, None, snapshot(world_version=4))
    session.sync = AsyncMock(return_value=session.world)
    task = asyncio.create_task(session.reconcile(outcome(), timeout=1))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not task.done()
    session.world = replace(session.world, world_version=5)
    session._world_updated.set()
    assert (await task).world_version == 5


@pytest.mark.parametrize('version', [0, 5])
async def test_reconcile_times_out_instead_of_trading_on_stale_stock(version):
    session = ClientSession(None, None, snapshot(world_version=4))
    session.sync = AsyncMock(return_value=session.world)
    with pytest.raises(asyncio.TimeoutError):
        await session.reconcile(outcome(version), timeout=0.01)


async def test_slow_renderer_does_not_block_ticks_or_build_a_render_queue():
    started = threading.Event()
    release = threading.Event()
    calls = []

    class SlowReport:
        def refresh_html(self):
            calls.append(threading.get_ident())
            started.set()
            assert release.wait(timeout=2)

    loop_thread = threading.get_ident()
    async with LiveSummaryRenderer(SlowReport(), interval=10) as renderer:
        renderer.request_refresh()
        try:
            assert await asyncio.to_thread(started.wait, 1)
            for _ in range(100):
                renderer.request_refresh()
                await asyncio.sleep(0)
            assert len(calls) == 1
            assert calls[0] != loop_thread
        finally:
            release.set()
    # One live render and one final render; no hundred-render backlog.
    assert len(calls) == 2


async def test_renderer_finishes_with_completed_report(tmp_path):
    path = tmp_path / 'run.jsonl'
    w = snapshot()
    with RunLog.open(path) as log:
        async with LiveSummaryRenderer(log) as renderer:
            log.run_started(w, mode='trade')
            renderer.request_refresh()
            log.run_ended(w, reason='finished', refresh=False)
    assert 'Live' not in path.with_name('run-summary.html').read_text()


async def test_runner_uses_settled_push_without_extra_sync_and_logs_latency(tmp_path):
    class PushedSession(ClientSession):
        def __init__(self):
            super().__init__(None, None, snapshot(inventory=(30, 30, 30)))
            self.sync = AsyncMock(side_effect=AssertionError('unnecessary sync'))

        async def send(self, *, request_id, message):
            wire = make_result(request_id=request_id)
            wire.processed_version = self.world.world_version + 1
            self.world = replace(self.world, world_version=wire.processed_version,
                                 snapshot_sequence=self.world.snapshot_sequence + 1,
                                 phase=pb.PHASE_FINISHED)
            return CommandOutcome.from_wire(wire)

    path = tmp_path / 'run.jsonl'
    session = PushedSession()
    with RunLog.open(path) as log:
        final = await run_trading(session, log, conservative=True)
    assert final.phase == pb.PHASE_FINISHED
    session.sync.assert_not_awaited()
    assert 'trade_timing' in path.read_text()


@pytest.mark.parametrize('tick_ms', [20, 5])
async def test_delayed_peer_trades_keep_planet_supplied_at_faster_ticks(tick_ms):
    """Real session pump/command correlation with upkeep and delayed peers.

    Command latency stays fixed while ticks run four times faster. Peers
    settle offers after a further delay; posting an offer adds no inventory.
    """
    queue = asyncio.Queue()
    inventory = [200, 15, 15]
    offers, transactions, results, settlement_tasks = [], [], [], []
    tick, version, sequence, health = 0, 1, 1, 100
    ads = [make_advertisement(station_id='P02', selling=[pb.RESOURCE_FOOD], expires_tick=100),
           make_advertisement(station_id='P03', selling=[pb.RESOURCE_COMPONENTS], expires_tick=100)]

    def state():
        wire = make_state(tick=tick, world_version=version, snapshot_sequence=sequence,
                          phase=pb.PHASE_FINISHED if tick >= 30 else pb.PHASE_RUNNING,
                          self_observation=make_self_observation(inventory=make_bundle(*inventory), health=health),
                          offers=offers, advertisements=ads, transactions=transactions,
                          request_results=results)
        wire.rules.tick_duration_ms = tick_ms
        wire.rules.duration_ticks = 30
        wire.rules.max_request_records_per_station = 1000
        return wire

    async def push():
        nonlocal sequence
        sequence += 1
        message = pb.ServerMessage()
        message.state.CopyFrom(state())
        await queue.put(message)

    async def settle(offer):
        nonlocal version
        await asyncio.sleep(0.02)
        if tick >= offer.expires_tick or offer.status != pb.OFFER_STATUS_OPEN:
            return
        give, receive = Bundle.from_wire(offer.give), Bundle.from_wire(offer.receive)
        assert Bundle(*inventory).covers(give)
        after = Bundle(*inventory).minus(give) + receive
        inventory[:] = [after.water, after.food, after.components]
        offer.status = pb.OFFER_STATUS_ACCEPTED
        transactions.append(make_transaction(transaction_id='tx-' + offer.offer_id,
                                             offer_id=offer.offer_id, recipient_id=offer.recipient_id,
                                             give=offer.give, receive=offer.receive, settled_tick=tick))
        version += 1
        await push()

    class Broker:
        async def send(self, message):
            nonlocal version
            kind = message.WhichOneof('message')
            if kind == 'sync':
                await push()
                return
            # Simulated wire/server processing delay, independent of tick speed.
            await asyncio.sleep(0.012)
            command = getattr(message, kind)
            object_id = command.request_id
            if kind == 'offer':
                offer = make_offer(offer_id=object_id, recipient_id=command.body.recipient_id,
                                   give=command.body.give, receive=command.body.receive,
                                   created_tick=tick, expires_tick=command.body.expires_tick)
                offers.append(offer)
                settlement_tasks.append(asyncio.create_task(settle(offer)))
            elif kind == 'advertise':
                ads[:] = [ad for ad in ads if ad.station_id != 'P01']
                ads.append(make_advertisement(station_id='P01',
                    selling=list(command.body.selling.items), seeking=list(command.body.seeking.items),
                    expires_tick=command.body.expires_tick))
            elif kind == 'withdraw':
                next(o for o in offers if o.offer_id == command.body.object_id).status = pb.OFFER_STATUS_WITHDRAWN
            else:
                raise AssertionError(kind)
            version += 1
            result = make_result(request_id=command.request_id, object_id=object_id)
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
        for _ in range(30):
            await asyncio.sleep(tick_ms / 1000)
            tick += 1
            inventory[0] += 3
            for r in range(3):
                if inventory[r] > 0:
                    inventory[r] -= 1
                else:
                    health -= 5
            version += 1
            await push()

    session = ClientSession(Broker(), messages(), WorldView.from_state(state()))
    session.start_pump()
    ticker = asyncio.create_task(ticking())
    try:
        final = await asyncio.wait_for(run_trading(session, conservative=True), timeout=3)
        assert final.phase == pb.PHASE_FINISHED
        assert final.self.health == 100
        assert len(transactions) >= 2
        assert any(tx.receive.food > 0 for tx in transactions)
        assert any(tx.receive.components > 0 for tx in transactions)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await asyncio.gather(*settlement_tasks)
        await session.stop_pump()
