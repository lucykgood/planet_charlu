import json

from fixtures import (
    make_advertisement,
    make_bundle,
    make_offer,
    make_result,
    make_self_observation,
    make_state,
    make_transaction,
)
from planet_charlu.domain.offers import Offer
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb
from planet_charlu.strategy import BaseStrategy
from planet_charlu.structured_log import RunLog


def world(inventory=(30, 2, 5), offers=(), ads=(), transactions=(), tick=0):
    state = make_state(
        tick=tick,
        offers=list(offers),
        advertisements=list(ads),
        transactions=list(transactions),
        self_observation=make_self_observation(inventory=make_bundle(*inventory)),
    )
    state.rules.max_request_records_per_station = 1000
    return WorldView.from_state(state)


def skip_ad(strategy, snapshot):
    action = strategy.choose(snapshot)
    assert action.message.WhichOneof("message") == "advertise"
    strategy.record(action)


def read_events(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def test_run_started_records_run_and_station_identity(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.run_started(world(), mode="trade")

    (event,) = read_events(path)
    assert event["event"] == "run_started"
    assert event["mode"] == "trade"
    assert event["self_station_id"] == "P01"
    assert event["starting_inventory"] == {"water": 30, "food": 2, "components": 5}


def test_tick_snapshot_records_inventory_and_budget(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.tick_snapshot(world(tick=3), sent_total=7)

    (event,) = read_events(path)
    assert event["event"] == "tick_snapshot"
    assert event["tick"] == 3
    assert event["budget_used"] == 7
    assert event["inventory"] == {"water": 30, "food": 2, "components": 5}


def test_decision_uses_key_prefix_as_kind_for_non_partner_actions(tmp_path):
    path = tmp_path / "run.jsonl"
    w = world()
    strategy = BaseStrategy()
    action = strategy.choose(w)
    assert action is not None
    assert action.key == "advertise"

    with RunLog.open(path) as run_log:
        run_log.decision(w, key=action.key, reason=action.reason, request_id=action.request_id)

    (event,) = read_events(path)
    assert event["event"] == "decision"
    assert event["kind"] == "advertise"
    assert event["reason"] == action.reason
    assert event["request_id"] == action.request_id


def test_decision_splits_partner_key_into_seek_trade_or_gift(tmp_path):
    # Same 'partner:<station>' key prefix covers two different strategy
    # actions -- seeking a needed resource vs. gifting surplus specialty --
    # distinguishable only by their reason text. The summary needs them
    # split apart, so RunLog.decision must do that splitting itself.
    path = tmp_path / "run.jsonl"

    seek_world = world(ads=[make_advertisement(station_id="TEAM-Z")])
    seek_strategy = BaseStrategy()
    seek_action = seek_strategy.choose(seek_world)
    assert seek_action.key == "partner:TEAM-Z"

    gift_world = world(
        inventory=(100, 30, 30),
        ads=[make_advertisement(station_id="TEAM-Z", selling=[], seeking=[pb.RESOURCE_WATER])],
    )
    gift_strategy = BaseStrategy()
    skip_ad(gift_strategy, gift_world)
    gift_action = gift_strategy.choose(gift_world)
    assert gift_action.key == "partner:TEAM-Z"

    with RunLog.open(path) as run_log:
        run_log.decision(seek_world, key=seek_action.key, reason=seek_action.reason,
                          request_id=seek_action.request_id)
        run_log.decision(gift_world, key=gift_action.key, reason=gift_action.reason,
                          request_id=gift_action.request_id)

    seek_event, gift_event = read_events(path)
    assert seek_event["kind"] == "seek_trade"
    assert gift_event["kind"] == "gift"


def test_decision_with_message_records_exact_offer_terms(tmp_path):
    path = tmp_path / "run.jsonl"
    w = world(ads=[make_advertisement(station_id="TEAM-Z")])
    strategy = BaseStrategy()
    action = strategy.choose(w)
    assert action.key == "partner:TEAM-Z"

    with RunLog.open(path) as run_log:
        run_log.decision(w, key=action.key, reason=action.reason,
                          request_id=action.request_id, message=action.message)

    (event,) = read_events(path)
    assert event["sent"]["command"] == "offer"
    assert event["sent"]["recipient_id"] == "TEAM-Z"
    assert event["sent"]["give"] == {"water": 4, "food": 0, "components": 0}
    assert event["sent"]["receive"] == {"water": 0, "food": 4, "components": 0}


def test_decision_without_message_omits_sent_field(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.decision(world(), key="step2", reason="advertise water for food", request_id="r1")

    (event,) = read_events(path)
    assert "sent" not in event


def test_command_result_records_outcome(tmp_path):
    path = tmp_path / "run.jsonl"
    w = world(ads=[make_advertisement(station_id="TEAM-Z")])
    strategy = BaseStrategy()
    action = strategy.choose(w)
    outcome = CommandOutcome.from_wire(
        make_result(request_id=action.request_id, ok=True, code=pb.RESULT_CODE_OK,
                    object_id="ad-1")
    )

    with RunLog.open(path) as run_log:
        run_log.command_result(w, key=action.key, outcome=outcome)

    (event,) = read_events(path)
    assert event["event"] == "command_result"
    assert event["ok"] is True
    assert event["code"] == "RESULT_CODE_OK"
    assert event["object_id"] == "ad-1"
    assert event["transaction_id"] is None


def test_offers_snapshot_records_open_offers_from_our_perspective(tmp_path):
    path = tmp_path / "run.jsonl"
    outgoing = make_offer(offer_id="o1", proposer_id="P01", recipient_id="P02",
                           give=make_bundle(water=4), receive=make_bundle(food=4), expires_tick=6)
    incoming = make_offer(offer_id="o2", proposer_id="P03", recipient_id="P01",
                           give=make_bundle(components=2), receive=make_bundle(water=2), expires_tick=6)
    w = world(offers=[outgoing, incoming])

    with RunLog.open(path) as run_log:
        run_log.offers_snapshot(w)

    (event,) = read_events(path)
    assert event["event"] == "offers_open"
    assert event["outgoing"] == [{"offer_id": "o1", "counterparty": "P02",
                                   "we_give": {"water": 4, "food": 0, "components": 0},
                                   "we_receive": {"water": 0, "food": 4, "components": 0},
                                   "expires_tick": 6}]
    assert event["incoming"] == [{"offer_id": "o2", "counterparty": "P03",
                                   "we_give": {"water": 2, "food": 0, "components": 0},
                                   "we_receive": {"water": 0, "food": 0, "components": 2},
                                   "expires_tick": 6}]


def test_offers_snapshot_skips_writing_when_nothing_open(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.offers_snapshot(world())

    assert read_events(path) == []


def test_offer_passed_records_reason_and_our_perspective(tmp_path):
    path = tmp_path / "run.jsonl"
    offer = make_offer(offer_id="o5", proposer_id="P07", recipient_id="P01",
                        give=make_bundle(food=1), receive=make_bundle(water=3), expires_tick=6)
    w = world(offers=[offer])

    with RunLog.open(path) as run_log:
        run_log.offer_passed(w, Offer.from_wire(offer), reason="unfavorable: would give more than we receive")

    (event,) = read_events(path)
    assert event["event"] == "offer_passed"
    assert event["offer_id"] == "o5"
    assert event["counterparty"] == "P07"
    assert event["we_would_give"] == {"water": 3, "food": 0, "components": 0}
    assert event["we_would_receive"] == {"water": 0, "food": 1, "components": 0}
    assert event["reason"] == "unfavorable: would give more than we receive"


def test_transactions_settled_reports_our_perspective_regardless_of_role(tmp_path):
    path = tmp_path / "run.jsonl"
    as_proposer = make_transaction(
        transaction_id="tx-1", proposer_id="P01", recipient_id="P02",
        give=make_bundle(water=4), receive=make_bundle(food=4), settled_tick=1,
    )
    as_recipient = make_transaction(
        transaction_id="tx-2", proposer_id="P03", recipient_id="P01",
        give=make_bundle(components=2), receive=make_bundle(water=2), settled_tick=2,
    )
    w = world(transactions=[as_proposer, as_recipient])

    with RunLog.open(path) as run_log:
        run_log.transactions_settled(w)

    events = read_events(path)
    assert [e["event"] for e in events] == ["transaction_settled", "transaction_settled"]

    first, second = events
    assert first["counterparty"] == "P02"
    assert first["we_gave"] == {"water": 4, "food": 0, "components": 0}
    assert first["we_received"] == {"water": 0, "food": 4, "components": 0}

    assert second["counterparty"] == "P03"
    assert second["we_gave"] == {"water": 2, "food": 0, "components": 0}
    assert second["we_received"] == {"water": 0, "food": 0, "components": 2}


def test_transactions_settled_is_idempotent_across_repeated_snapshots(tmp_path):
    path = tmp_path / "run.jsonl"
    tx = make_transaction(transaction_id="tx-1", settled_tick=1)
    w = world(transactions=[tx])

    with RunLog.open(path) as run_log:
        run_log.transactions_settled(w)
        run_log.transactions_settled(w)  # same snapshot reappears; must not double-log

    events = read_events(path)
    assert len(events) == 1


def test_run_ended_records_final_state(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.run_ended(world(inventory=(28, 31, 31)), reason="phase finished")

    (event,) = read_events(path)
    assert event["event"] == "run_ended"
    assert event["reason"] == "phase finished"
    assert event["final_inventory"] == {"water": 28, "food": 31, "components": 31}


def test_events_append_across_multiple_opens(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.run_started(world(), mode="trade")
    with RunLog.open(path) as run_log:
        run_log.run_ended(world(), reason="phase finished")

    events = read_events(path)
    assert [e["event"] for e in events] == ["run_started", "run_ended"]


def test_refresh_html_writes_a_live_summary_next_to_the_log(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.run_started(world(), mode="trade")
        run_log.refresh_html()

        html_path = tmp_path / "run-summary.html"
        assert html_path.exists()
        assert "Live" in html_path.read_text(encoding="utf-8")  # no run_ended yet

        # run_ended() always refreshes itself -- a caller that forgets to
        # call refresh_html() separately must not leave the page stuck live.
        run_log.run_ended(world(), reason="phase finished")
        assert "Live" not in html_path.read_text(encoding="utf-8")


def test_refresh_html_never_raises_even_if_rendering_fails(tmp_path, monkeypatch, caplog):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        import planet_charlu.run_summary as run_summary_module

        def boom(*args, **kwargs):
            raise RuntimeError("rendering exploded")

        monkeypatch.setattr(run_summary_module, "write_summary", boom)
        with caplog.at_level("ERROR"):
            result = run_log.refresh_html()  # must not propagate

    assert result is None
    assert "failed to refresh HTML summary" in caplog.text


def test_refresh_html_returns_the_written_path(tmp_path):
    path = tmp_path / "run.jsonl"
    with RunLog.open(path) as run_log:
        run_log.run_started(world(), mode="trade")
        result = run_log.refresh_html()

    assert result == tmp_path / "run-summary.html"


def test_refresh_html_opens_a_browser_once_on_first_successful_write(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("planet_charlu.structured_log.webbrowser.open", lambda url: opened.append(url))
    path = tmp_path / "run.jsonl"

    with RunLog.open(path, open_browser=True) as run_log:
        run_log.run_started(world(), mode="trade")
        run_log.refresh_html()
        run_log.refresh_html()  # a later tick's refresh must not reopen it

    assert len(opened) == 1
    assert opened[0] == (tmp_path / "run-summary.html").resolve().as_uri()


def test_refresh_html_does_not_open_a_browser_unless_requested(tmp_path, monkeypatch):
    def fail_if_called(url):
        raise AssertionError("must not open a browser when open_browser=False")

    monkeypatch.setattr("planet_charlu.structured_log.webbrowser.open", fail_if_called)
    path = tmp_path / "run.jsonl"

    with RunLog.open(path) as run_log:  # open_browser defaults to False
        run_log.run_started(world(), mode="trade")
        run_log.refresh_html()


def test_refresh_html_does_not_mark_opened_when_the_write_that_would_show_it_fails(tmp_path, monkeypatch):
    import planet_charlu.run_summary as run_summary_module

    opened = []
    monkeypatch.setattr("planet_charlu.structured_log.webbrowser.open", lambda url: opened.append(url))
    original_write_summary = run_summary_module.write_summary
    path = tmp_path / "run.jsonl"

    with RunLog.open(path, open_browser=True) as run_log:
        def boom(*args, **kwargs):
            raise RuntimeError("rendering exploded")

        run_summary_module.write_summary = boom
        try:
            run_log.refresh_html()  # fails to render; nothing to open yet
        finally:
            run_summary_module.write_summary = original_write_summary
        run_log.refresh_html()  # first real success -- this is the one to open

    assert len(opened) == 1
