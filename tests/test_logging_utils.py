from planet_charlu import codec
from planet_charlu.domain.offers import Offer
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2
from planet_charlu.logging_utils import (
    TICK_SEPARATOR,
    describe_server_message,
    format_offer_review,
    format_result_extra,
    format_sent_terms,
    format_status_report,
    humanize_phase,
    humanize_result_code,
)

from fixtures import (
    make_advertisement,
    make_bundle,
    make_offer,
    make_protocol_error,
    make_readiness,
    make_result,
    make_self_observation,
    make_state,
    make_transaction,
)


def test_describe_state_includes_phase_and_health():
    message = bazaar_pb2.ServerMessage()
    message.state.CopyFrom(make_state(phase=bazaar_pb2.PHASE_RUNNING))

    summary = describe_server_message(message)

    assert "phase=running" in summary
    assert "health=100" in summary
    assert "self=P01" in summary


def test_describe_state_includes_specialty_and_inventory():
    message = bazaar_pb2.ServerMessage()
    message.state.CopyFrom(make_state())

    summary = describe_server_message(message)

    assert "specialty=water" in summary
    assert "inventory=(water=30,food=30,components=30)" in summary


def test_describe_result_includes_ok_and_code():
    message = bazaar_pb2.ServerMessage()
    message.result.CopyFrom(make_result(ok=True, code=bazaar_pb2.RESULT_CODE_OK))

    summary = describe_server_message(message)

    assert "ok=True" in summary
    assert "RESULT_CODE_OK" in summary


def test_describe_readiness_includes_ready_flag():
    message = bazaar_pb2.ServerMessage()
    message.readiness.CopyFrom(make_readiness(ready=True))

    summary = describe_server_message(message)

    assert "ready=True" in summary


def test_describe_protocol_error_includes_code_and_close_session():
    message = bazaar_pb2.ServerMessage()
    message.protocol_error.CopyFrom(
        make_protocol_error(
            code=bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, close_session=False
        )
    )

    summary = describe_server_message(message)

    assert "CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED" in summary
    assert "close_session=False" in summary


def test_humanize_phase_strips_prefix_and_lowercases():
    assert humanize_phase(bazaar_pb2.PHASE_RUNNING) == "running"
    assert humanize_phase(bazaar_pb2.PHASE_FINISHED) == "finished"


def test_humanize_result_code_strips_prefix_and_lowercases():
    assert humanize_result_code(bazaar_pb2.RESULT_CODE_OK) == "ok"
    assert humanize_result_code(bazaar_pb2.RESULT_CODE_RATE_LIMITED) == "rate limited"
    assert humanize_result_code(bazaar_pb2.RESULT_CODE_INSUFFICIENT_RESOURCES) == "insufficient resources"


def test_describe_unset_message_does_not_raise():
    message = bazaar_pb2.ServerMessage()

    summary = describe_server_message(message)

    assert "unset" in summary


def test_status_report_shows_epoch_and_critical_reserve():
    state = make_state(
        tick=7,
        self_observation=make_self_observation(
            inventory=make_bundle(30, 2, 5), upkeep_per_tick=make_bundle(1, 1, 1),
        ),
    )
    report = format_status_report(WorldView.from_state(state))

    assert report.startswith(TICK_SEPARATOR + "\ntick 7 | ")
    assert "food=2 (reserve 3, CRITICAL)" in report
    assert "water=30 (reserve 3, ok)" in report


def test_status_report_lists_pending_actions_and_open_offers():
    outgoing = make_offer(offer_id="o1", proposer_id="P01", recipient_id="P02",
                           give=make_bundle(water=4), receive=make_bundle(food=4), expires_tick=6)
    incoming = make_offer(offer_id="o2", proposer_id="P03", recipient_id="P01",
                           give=make_bundle(components=2), receive=make_bundle(water=2), expires_tick=6)
    ad = make_advertisement(station_id="P01", selling=[bazaar_pb2.RESOURCE_WATER],
                             seeking=[bazaar_pb2.RESOURCE_FOOD])
    state = make_state(tick=0, offers=[outgoing, incoming], advertisements=[ad])
    report = format_status_report(WorldView.from_state(state))

    assert "pending actions: 1 offer(s) awaiting response, 1 active advertisement(s)" in report
    assert "-> offer to P02: give 4 water, receive 4 food" in report
    assert "-> advertising: selling water, seeking food" in report
    assert "open offers to us (1):" in report
    assert "<- offer from P03: we'd give 2 water, we'd receive 2 components" in report


def test_status_report_shows_no_pending_or_open_offers_when_absent():
    state = make_state(tick=0)
    report = format_status_report(WorldView.from_state(state))

    assert "pending actions: none" in report
    assert "open offers to us: none" in report
    assert "recent trades: none yet" in report


def test_status_report_shows_recent_trades_from_our_perspective():
    as_proposer = make_transaction(transaction_id="tx-1", proposer_id="P01", recipient_id="P02",
                                    give=make_bundle(water=4), receive=make_bundle(food=4), settled_tick=1)
    as_recipient = make_transaction(transaction_id="tx-2", proposer_id="P03", recipient_id="P01",
                                     give=make_bundle(components=2), receive=make_bundle(water=2), settled_tick=2)
    state = make_state(tick=3, transactions=[as_proposer, as_recipient])
    report = format_status_report(WorldView.from_state(state))

    assert "recent trades (last 2):" in report
    assert "tick 2: gave 2 water, received 2 components <-> P03" in report
    assert "tick 1: gave 4 water, received 4 food <-> P02" in report


def test_format_sent_terms_for_each_command_kind():
    offer_message = codec.build_offer(run_id="r", request_id="req", recipient_id="P02",
                                       give=Bundle(water=4), receive=Bundle(food=4), expires_tick=12)
    assert format_sent_terms(offer_message) == "give 4 water, receive 4 food to P02 (expires tick 12)"

    accept_message = codec.build_accept(run_id="r", request_id="req", offer_id="offer-9")
    assert format_sent_terms(accept_message) == "offer offer-9"

    withdraw_message = codec.build_withdraw(run_id="r", request_id="req", object_id="ad-3")
    assert format_sent_terms(withdraw_message) == "object ad-3"

    advertise_message = codec.build_advertise(run_id="r", request_id="req", selling=[Resource.WATER],
                                               seeking=[Resource.FOOD], expires_tick=10)
    assert format_sent_terms(advertise_message) == "selling water, seeking food (expires tick 10)"


def test_format_result_extra_shows_object_and_transaction_ids():
    assert format_result_extra(CommandOutcome.from_wire(make_result())) == ""

    with_object = CommandOutcome.from_wire(make_result(object_id="ad-1"))
    assert format_result_extra(with_object) == " (object ad-1)"

    with_both = CommandOutcome.from_wire(make_result(object_id="ad-1", transaction_id="tx-2"))
    assert format_result_extra(with_both) == " (object ad-1, transaction tx-2)"


def test_format_offer_review_lists_only_declined_offers():
    accepted = Offer.from_wire(make_offer(offer_id="o1"))
    declined = Offer.from_wire(make_offer(offer_id="o2", proposer_id="P05", recipient_id="P01",
                                           give=make_bundle(food=1), receive=make_bundle(water=3)))
    state = make_state()
    world = WorldView.from_state(state)

    line = format_offer_review(world, [(accepted, "accepted"), (declined, "unfavorable: would give more than we receive")])

    assert "passed on 1 incoming offer(s):" in line
    assert "o1" not in line
    assert "offer from P05: we'd give 3 water, we'd receive 1 food -- unfavorable" in line


def test_format_offer_review_returns_none_when_everything_was_accepted():
    accepted = Offer.from_wire(make_offer(offer_id="o1"))
    state = make_state()
    world = WorldView.from_state(state)

    assert format_offer_review(world, [(accepted, "accepted")]) is None
