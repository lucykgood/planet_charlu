from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.transactions import Transaction

from fixtures import make_bundle, make_transaction


def test_from_wire_maps_all_fields():
    wire = make_transaction(
        transaction_id="txn-1",
        offer_id="offer-1",
        proposer_id="P01",
        recipient_id="P02",
        give=make_bundle(2),
        receive=make_bundle(0, 1),
        settled_tick=3,
    )

    transaction = Transaction.from_wire(wire)

    assert transaction.transaction_id == "txn-1"
    assert transaction.offer_id == "offer-1"
    assert transaction.give == Bundle(water=2)
    assert transaction.receive == Bundle(food=1)
    assert transaction.settled_tick == 3


def test_involves_checks_both_sides():
    transaction = Transaction.from_wire(make_transaction(proposer_id="P01", recipient_id="P02"))

    assert transaction.involves("P01")
    assert transaction.involves("P02")
    assert not transaction.involves("P03")
