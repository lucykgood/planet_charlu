from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2

from fixtures import (
    make_advertisement,
    make_bundle,
    make_offer,
    make_result,
    make_self_observation,
    make_state,
    make_transaction,
)


def test_from_state_maps_top_level_fields():
    state = make_state(run_id="run-1", tick=3, phase=bazaar_pb2.PHASE_RUNNING)

    world = WorldView.from_state(state)

    assert world.run_id == "run-1"
    assert world.tick == 3
    assert world.self_station_id == "P01"
    assert world.self.inventory == Bundle(30, 30, 30)


def test_is_running_reflects_phase():
    running = WorldView.from_state(make_state(phase=bazaar_pb2.PHASE_RUNNING))
    paused = WorldView.from_state(make_state(phase=bazaar_pb2.PHASE_PAUSED))

    assert running.is_running()
    assert not paused.is_running()


def test_offers_advertisements_transactions_and_results_are_all_decoded():
    state = make_state(
        offers=[make_offer(offer_id="offer-1")],
        advertisements=[make_advertisement(advertisement_id="advertisement-1")],
        transactions=[make_transaction(transaction_id="transaction-1")],
        request_results=[make_result(request_id="req-1")],
    )

    world = WorldView.from_state(state)

    assert [o.offer_id for o in world.offers] == ["offer-1"]
    assert [a.advertisement_id for a in world.advertisements] == ["advertisement-1"]
    assert [t.transaction_id for t in world.transactions] == ["transaction-1"]
    assert [r.request_id for r in world.request_results] == ["req-1"]


def test_open_offers_from_me_excludes_others_and_non_open():
    state = make_state(
        station_id="P01",
        offers=[
            make_offer(offer_id="mine-open", proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN),
            make_offer(
                offer_id="mine-withdrawn",
                proposer_id="P01",
                status=bazaar_pb2.OFFER_STATUS_WITHDRAWN,
            ),
            make_offer(offer_id="theirs-open", proposer_id="P02", status=bazaar_pb2.OFFER_STATUS_OPEN),
        ],
    )

    world = WorldView.from_state(state)

    assert [o.offer_id for o in world.open_offers_from_me()] == ["mine-open"]


def test_open_offers_to_me_only_includes_offers_addressed_to_self():
    state = make_state(
        station_id="P01",
        offers=[
            make_offer(offer_id="to-me", proposer_id="P02", recipient_id="P01"),
            make_offer(offer_id="to-someone-else", proposer_id="P02", recipient_id="P03"),
        ],
    )

    world = WorldView.from_state(state)

    assert [o.offer_id for o in world.open_offers_to_me()] == ["to-me"]


def test_committed_bundle_sums_only_my_open_offers():
    state = make_state(
        station_id="P01",
        offers=[
            make_offer(proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(2)),
            make_offer(proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(3)),
            make_offer(
                proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_WITHDRAWN, give=make_bundle(100)
            ),
            make_offer(proposer_id="P02", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(50)),
        ],
    )

    world = WorldView.from_state(state)

    assert world.committed_bundle() == Bundle(water=5)


def test_available_bundle_subtracts_commitments_from_inventory():
    state = make_state(
        station_id="P01",
        self_observation=make_self_observation("P01", inventory=make_bundle(30, 30, 30)),
        offers=[make_offer(proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(12))],
    )

    world = WorldView.from_state(state)

    assert world.available_bundle() == Bundle(water=18, food=30, components=30)


def test_available_bundle_clamps_at_zero_when_overcommitted():
    state = make_state(
        station_id="P01",
        self_observation=make_self_observation("P01", inventory=make_bundle(5, 5, 5)),
        offers=[
            make_offer(proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(4)),
            make_offer(proposer_id="P01", status=bazaar_pb2.OFFER_STATUS_OPEN, give=make_bundle(4)),
        ],
    )

    world = WorldView.from_state(state)

    assert world.available_bundle() == Bundle(water=0, food=5, components=5)


def test_a_newer_snapshot_produces_an_independent_view_rather_than_merging():
    first = WorldView.from_state(
        make_state(snapshot_sequence=1, offers=[make_offer(offer_id="offer-1")])
    )
    second = WorldView.from_state(make_state(snapshot_sequence=2, offers=[]))

    # Replacing the reference (as a caller would) leaves no trace of the
    # older snapshot's offers -- there is no in-place merge to get wrong.
    assert [o.offer_id for o in first.offers] == ["offer-1"]
    assert second.offers == ()
    assert second.snapshot_sequence == 2
