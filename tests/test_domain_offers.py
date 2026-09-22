from planet_charlu.domain.offers import Offer, OfferStatus
from planet_charlu.domain.resources import Bundle
from planet_charlu.generated import bazaar_pb2

from fixtures import make_bundle, make_offer


def test_from_wire_maps_fields_and_perspective():
    wire = make_offer(
        offer_id="offer-1",
        proposer_id="P01",
        recipient_id="P02",
        give=make_bundle(2),
        receive=make_bundle(0, 1),
        expires_tick=6,
    )

    offer = Offer.from_wire(wire)

    assert offer.offer_id == "offer-1"
    assert offer.proposer_id == "P01"
    assert offer.recipient_id == "P02"
    assert offer.give == Bundle(water=2)
    assert offer.receive == Bundle(food=1)
    assert offer.status is OfferStatus.OPEN
    assert offer.transaction_id is None


def test_from_wire_reads_transaction_id_when_present():
    wire = make_offer(status=bazaar_pb2.OFFER_STATUS_ACCEPTED, transaction_id="txn-1")

    offer = Offer.from_wire(wire)

    assert offer.transaction_id == "txn-1"
    assert offer.status is OfferStatus.ACCEPTED


def test_is_open_only_true_for_open_status():
    open_offer = Offer.from_wire(make_offer(status=bazaar_pb2.OFFER_STATUS_OPEN))
    withdrawn_offer = Offer.from_wire(make_offer(status=bazaar_pb2.OFFER_STATUS_WITHDRAWN))

    assert open_offer.is_open()
    assert not withdrawn_offer.is_open()


def test_expiry_is_exclusive_at_the_boundary_tick():
    offer = Offer.from_wire(make_offer(expires_tick=12))

    assert not offer.is_expired_by(11)
    assert offer.is_expired_by(12)
    assert offer.is_expired_by(13)


def test_is_gift_when_receive_is_all_zero():
    gift = Offer.from_wire(make_offer(give=make_bundle(0, 0, 1), receive=make_bundle()))
    paid_offer = Offer.from_wire(make_offer(give=make_bundle(2), receive=make_bundle(0, 1)))

    assert gift.is_gift()
    assert not paid_offer.is_gift()


def test_proposed_by_and_directed_to():
    offer = Offer.from_wire(make_offer(proposer_id="P01", recipient_id="P02"))

    assert offer.proposed_by("P01")
    assert not offer.proposed_by("P02")
    assert offer.directed_to("P02")
    assert not offer.directed_to("P01")
