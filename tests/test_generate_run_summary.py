"""Exercises planet_charlu.run_summary against the JSON Lines schema directly
(plain dicts, no dependency on planet_charlu.structured_log) -- this is also
a regression check that the module stays decodable by anyone who only has
the schema, not the client's Python types.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from planet_charlu import run_summary

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "generate_run_summary.py"


def _load_cli_script():
    spec = importlib.util.spec_from_file_location("generate_run_summary_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle(water=0, food=0, components=0):
    return {"water": water, "food": food, "components": components}


SAMPLE_EVENTS = [
    {"event": "run_started", "tick": 0, "run_id": "run-1", "mode": "trade",
     "self_station_id": "P01", "specialty": "water", "starting_inventory": bundle(30, 2, 5)},
    {"event": "tick_snapshot", "tick": 0, "health": 100, "inventory": bundle(30, 2, 5),
     "budget_used": 0, "budget_max": 100},
    {"event": "tick_snapshot", "tick": 1, "health": 100, "inventory": bundle(26, 6, 5),
     "budget_used": 2, "budget_max": 100},
    {"event": "decision", "tick": 0, "key": "advertise", "kind": "advertise",
     "reason": "publish specialty surplus and unproduced-resource needs", "request_id": "r1",
     "sent": {"command": "advertise", "selling": ["water"], "seeking": ["food"], "expires_tick": 10}},
    {"event": "decision", "tick": 1, "key": "partner:P02", "kind": "seek_trade",
     "reason": "trade water for needed food with P02", "request_id": "r2",
     "sent": {"command": "offer", "recipient_id": "P02", "give": bundle(water=4),
              "receive": bundle(food=4), "expires_tick": 6}},
    {"event": "command_result", "tick": 0, "key": "advertise", "request_id": "r1",
     "ok": True, "code": "RESULT_CODE_OK", "retry_after_tick": None},
    {"event": "command_result", "tick": 1, "key": "partner:P02", "request_id": "r2",
     "ok": False, "code": "RESULT_CODE_RATE_LIMITED", "retry_after_tick": 5},
    {"event": "transaction_settled", "tick": 1, "transaction_id": "tx-1", "offer_id": "o1",
     "counterparty": "P02", "we_gave": bundle(water=4), "we_received": bundle(food=4)},
    {"event": "run_ended", "tick": 1, "reason": "phase finished or station failed",
     "health": 100, "failed_once": False, "final_inventory": bundle(26, 6, 5), "transaction_count": 1},
]


def test_load_events_skips_malformed_lines(tmp_path, capsys):
    path = tmp_path / "run.jsonl"
    path.write_text('{"event": "run_started", "tick": 0}\nnot json\n{"event": "run_ended", "tick": 1}\n')

    events = run_summary.load_events(path)

    assert [e["event"] for e in events] == ["run_started", "run_ended"]
    assert "skipping unparseable line" in capsys.readouterr().err


def test_run_summary_aggregates_all_event_kinds():
    summary = run_summary.RunSummary(SAMPLE_EVENTS)

    assert summary.station_id == "P01"
    assert summary.specialty == "water"
    assert not summary.incomplete
    assert summary.final["final_inventory"] == bundle(26, 6, 5)

    counts = summary.decision_counts()
    assert counts == {"advertise": 1, "seek_trade": 1}

    ok, failed, by_code = summary.command_outcome_counts()
    assert (ok, failed) == (1, 1)
    assert by_code == {"RESULT_CODE_RATE_LIMITED": 1}

    flow = summary.resource_flow()
    assert flow["water"] == {"gave": 4, "received": 0}
    assert flow["food"] == {"gave": 0, "received": 4}

    assert summary.partner_volumes() == [("P02", 8)]


def test_decision_log_joins_reason_terms_and_outcome():
    summary = run_summary.RunSummary(SAMPLE_EVENTS)

    rows = summary.decision_log()

    assert len(rows) == 2
    advertise_row, offer_row = rows
    assert advertise_row["kind"] == "advertise"
    assert advertise_row["reason"] == "publish specialty surplus and unproduced-resource needs"
    assert advertise_row["terms"] == "selling water; seeking food (expires tick 10)"
    assert advertise_row["outcome"] == "ok"
    assert advertise_row["outcome_ok"] is True

    assert offer_row["kind"] == "seek_trade"
    assert offer_row["counterparty"] == "P02"
    assert offer_row["terms"] == "give 4w / 0f / 0c to P02, receive 0w / 4f / 0c (expires tick 6)"
    assert offer_row["outcome"] == "failed: RESULT_CODE_RATE_LIMITED, retry after tick 5"
    assert offer_row["outcome_ok"] is False


def test_decision_log_marks_missing_command_result_as_pending():
    events = [e for e in SAMPLE_EVENTS if not (e["event"] == "command_result" and e["request_id"] == "r1")]
    summary = run_summary.RunSummary(events)

    advertise_row = next(row for row in summary.decision_log() if row["kind"] == "advertise")
    assert advertise_row["outcome"] == "pending"
    assert advertise_row["outcome_ok"] is None


def test_decision_log_truncates_long_ids_but_keeps_the_full_value_on_hover():
    events = SAMPLE_EVENTS + [
        {"event": "decision", "tick": 2, "key": "accept:o5", "kind": "accept",
         "reason": "accept free gift", "request_id": "r3",
         "sent": {"command": "accept", "offer_id": "o5"}},
        {"event": "command_result", "tick": 2, "key": "accept:o5", "request_id": "r3",
         "ok": True, "code": "RESULT_CODE_OK",
         "transaction_id": "11b7a2b2-7da5-4472-a783-daa9935a42b8", "retry_after_tick": None},
    ]
    summary = run_summary.RunSummary(events)

    accept_row = next(row for row in summary.decision_log() if row["kind"] == "accept")
    assert accept_row["outcome"] == 'ok (<span title="11b7a2b2-7da5-4472-a783-daa9935a42b8">11b7a2b2&hellip;</span>)'


def test_render_html_includes_decision_log_table():
    html = run_summary.render_html(run_summary.RunSummary(SAMPLE_EVENTS))

    assert "Decision log" in html
    assert "trade water for needed food with P02" in html
    assert "publish specialty surplus and unproduced-resource needs" in html
    assert 'id="decision-filter"' in html
    assert 'data-target="decision-table"' in html


def test_run_summary_marks_incomplete_without_run_ended():
    events = [e for e in SAMPLE_EVENTS if e["event"] != "run_ended"]
    summary = run_summary.RunSummary(events)

    assert summary.incomplete
    # Falls back to the latest tick snapshot as the closest thing to a final state.
    assert summary.final["tick"] == 1


def test_run_summary_handles_zero_events_without_raising():
    summary = run_summary.RunSummary([])

    assert summary.station_id == "unknown"
    assert summary.incomplete
    assert summary.final == {}
    html = run_summary.render_html(summary)
    assert "<html" in html
    assert "No settled trades yet." in html


def test_render_html_embeds_valid_chart_json_and_key_figures():
    summary = run_summary.RunSummary(SAMPLE_EVENTS)
    html = run_summary.render_html(summary)

    assert "<html" in html and "</html>" in html
    match = re.search(
        r'<script id="chart-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["inventory-chart"]["ticks"] == [0, 1]
    assert payload["inventory-chart"]["series"]["water"] == [30, 26]

    assert "P01" in html
    assert "P02" in html  # partner volume / transaction table


def test_render_html_auto_refreshes_while_the_run_is_incomplete():
    events = [e for e in SAMPLE_EVENTS if e["event"] != "run_ended"]
    html = run_summary.render_html(run_summary.RunSummary(events))

    assert f'<meta http-equiv="refresh" content="{run_summary.LIVE_REFRESH_SECONDS}">' in html
    assert "Live" in html


def test_render_html_stops_auto_refreshing_once_the_run_has_ended():
    html = run_summary.render_html(run_summary.RunSummary(SAMPLE_EVENTS))

    assert "http-equiv=\"refresh\"" not in html


def test_write_summary_writes_output_file_next_to_log(tmp_path):
    log_path = tmp_path / "run.jsonl"
    log_path.write_text("\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n")

    out_path = run_summary.write_summary(log_path)

    assert out_path == tmp_path / "run-summary.html"
    assert "<html" in out_path.read_text(encoding="utf-8")


def test_write_summary_honors_explicit_out_path(tmp_path):
    log_path = tmp_path / "run.jsonl"
    log_path.write_text(json.dumps(SAMPLE_EVENTS[0]) + "\n")
    out_path = tmp_path / "custom.html"

    result = run_summary.write_summary(log_path, out_path)

    assert result == out_path
    assert out_path.exists()


def test_cli_writes_output_file_next_to_log(tmp_path):
    log_path = tmp_path / "run.jsonl"
    log_path.write_text("\n".join(json.dumps(e) for e in SAMPLE_EVENTS) + "\n")
    cli = _load_cli_script()

    argv = sys.argv
    sys.argv = ["generate_run_summary.py", str(log_path)]
    try:
        cli.main()
    finally:
        sys.argv = argv

    out_path = tmp_path / "run-summary.html"
    assert out_path.exists()
    assert "<html" in out_path.read_text(encoding="utf-8")


def test_cli_raises_on_empty_log(tmp_path):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("")
    cli = _load_cli_script()

    argv = sys.argv
    sys.argv = ["generate_run_summary.py", str(log_path)]
    try:
        with pytest.raises(SystemExit):
            cli.main()
    finally:
        sys.argv = argv


# Events for the Timeline tab: a shortage lasting ticks 1-2, then a gap of
# 48 real seconds (against a ~1s median pace) before tick 3, where a trade
# settles, our own offer gets rate-limited, and we decline an incoming offer.
TIMELINE_EVENTS = [
    {"event": "run_started", "tick": 0, "wall_time": 1000.0, "run_id": "run-2", "mode": "trade",
     "self_station_id": "P01", "specialty": "water"},
    {"event": "tick_snapshot", "tick": 0, "wall_time": 1000.0, "health": 100, "inventory": bundle(10, 10, 10),
     "last_unmet_upkeep": bundle(), "current_shortage_streak": 0, "budget_used": 0, "budget_max": 10},
    {"event": "tick_snapshot", "tick": 1, "wall_time": 1001.0, "health": 100, "inventory": bundle(9, 9, 9),
     "last_unmet_upkeep": bundle(water=1), "current_shortage_streak": 1, "budget_used": 0, "budget_max": 10},
    {"event": "tick_snapshot", "tick": 2, "wall_time": 1002.0, "health": 90, "inventory": bundle(8, 8, 8),
     "last_unmet_upkeep": bundle(water=1, food=1), "current_shortage_streak": 2, "budget_used": 0, "budget_max": 10},
    {"event": "tick_snapshot", "tick": 3, "wall_time": 1050.0, "health": 90, "inventory": bundle(8, 8, 8),
     "last_unmet_upkeep": bundle(), "current_shortage_streak": 0, "budget_used": 0, "budget_max": 10},
    {"event": "decision", "tick": 3, "wall_time": 1050.0, "key": "partner:P02", "kind": "seek_trade",
     "reason": "trade for food", "request_id": "r2",
     "sent": {"command": "offer", "recipient_id": "P02", "give": bundle(water=2),
              "receive": bundle(food=2), "expires_tick": 10}},
    {"event": "command_result", "tick": 3, "wall_time": 1050.5, "key": "partner:P02", "request_id": "r2",
     "ok": False, "code": "RESULT_CODE_RATE_LIMITED", "retry_after_tick": 6},
    {"event": "offer_passed", "tick": 3, "wall_time": 1050.6, "offer_id": "o9", "counterparty": "P05",
     "we_would_give": bundle(water=3), "we_would_receive": bundle(food=1), "reason": "unfavorable"},
    {"event": "transaction_settled", "tick": 3, "wall_time": 1051.0, "transaction_id": "tx-1", "offer_id": "o1",
     "counterparty": "P02", "we_gave": bundle(water=2), "we_received": bundle(food=2)},
    {"event": "run_ended", "tick": 3, "wall_time": 1051.0, "reason": "done", "health": 90,
     "failed_once": False, "final_inventory": bundle(6, 10, 8), "transaction_count": 1},
]


def test_shortage_periods_groups_consecutive_ticks_of_unmet_upkeep():
    summary = run_summary.RunSummary(TIMELINE_EVENTS)

    periods = summary.shortage_periods()

    assert periods == [{"start": 1, "end": 2, "resources": {"water", "food"}, "max_streak": 2}]


def test_disconnected_periods_flags_a_gap_much_longer_than_the_run_s_pace():
    summary = run_summary.RunSummary(TIMELINE_EVENTS)

    periods = summary.disconnected_periods()

    assert len(periods) == 1
    assert periods[0]["start"] == 2
    assert periods[0]["end"] == 3
    assert periods[0]["gap_seconds"] == pytest.approx(48.0)


def test_disconnected_periods_ignores_a_run_s_normal_tick_to_tick_jitter():
    events = [
        {"event": "tick_snapshot", "tick": t, "wall_time": 1000.0 + t * 1.0,
         "health": 100, "inventory": bundle(10, 10, 10), "last_unmet_upkeep": bundle(),
         "current_shortage_streak": 0, "budget_used": 0, "budget_max": 10}
        for t in range(5)
    ]

    summary = run_summary.RunSummary(events)

    assert summary.disconnected_periods() == []


def test_timeline_events_covers_trades_rejections_shortages_and_disconnects():
    summary = run_summary.RunSummary(TIMELINE_EVENTS)

    events = summary.timeline_events()
    by_type = {e["type"]: e for e in events}

    assert set(by_type) == {"trade", "rejected", "shortage", "disconnected"}
    assert by_type["trade"]["title"] == "Trade settled with P02"
    assert "unfavorable" in by_type["rejected"]["detail"] or "RATE_LIMITED" in by_type["rejected"]["detail"]
    assert by_type["shortage"]["start"] == 1 and by_type["shortage"]["end"] == 2
    assert by_type["disconnected"]["start"] == 2 and by_type["disconnected"]["end"] == 3
    # Two distinct rejections (our failed offer + the incoming one we declined).
    assert sum(1 for e in events if e["type"] == "rejected") == 2
    # Chronological: nothing before a shortage starting at tick 1.
    assert events == sorted(events, key=lambda e: (e["start"], e["end"]))


def test_render_html_includes_timeline_tab_and_events():
    html = run_summary.render_html(run_summary.RunSummary(TIMELINE_EVENTS))

    assert 'id="tab-timeline"' in html
    assert 'data-tab="timeline"' in html
    assert "timeline-trade" in html
    assert "timeline-rejected" in html
    assert "timeline-shortage" in html
    assert "timeline-disconnected" in html
    assert "Possible stall or disconnect" in html


def test_render_html_timeline_tab_is_empty_but_valid_without_notable_events():
    html = run_summary.render_html(run_summary.RunSummary([]))

    assert "No timeline events recorded." in html
