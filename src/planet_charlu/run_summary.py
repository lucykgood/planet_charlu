"""Renders an interactive HTML summary from a structured run log.

Reads the newline-delimited JSON events written by ``structured_log.RunLog``
(one JSON object per line; see ``docs/task7-structured-log-proposal.md`` for
the event schema) and builds a single self-contained HTML file -- inventory
and health over time, a decision/command/trade breakdown, and a filterable
transaction table.

Deliberately reads only plain dicts parsed from JSON -- no dependency on any
other ``planet_charlu`` module or third-party library -- so this module (or a
rewrite of it) can summarize a teammate's log even if they didn't use this
client, and ``scripts/generate_run_summary.py`` can keep working as a
dependency-free drop-in script for someone without a working environment.

``main.py`` calls :func:`write_summary` directly so every run/simulation gets
an HTML dashboard automatically; ``scripts/generate_run_summary.py`` is a thin
CLI wrapper around the same function for regenerating one by hand.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

RESOURCES = ("water", "food", "components")

# planet_charlu/docs dataviz palette -- categorical slots 1-3 (blue/orange/aqua)
# for the three resources, status good/critical for command outcomes. See
# ../../docs/task7-structured-log-proposal.md for why these specific roles.
COLOR_WATER = "#2a78d6"
COLOR_FOOD = "#eb6834"
COLOR_COMPONENTS = "#1baf7a"
COLOR_HEALTH = "#2a78d6"
COLOR_GOOD = "#0ca30c"
COLOR_CRITICAL = "#d03b3b"
DECISION_KIND_COLORS = {
    "withdraw": "#e34948",
    "accept": "#1baf7a",
    "advertise": "#eda100",
    "seek_trade": "#2a78d6",
    "gift": "#e87ba4",
}
DECISION_KIND_ORDER = ("withdraw", "accept", "advertise", "seek_trade", "gift")

# Timeline tab event types -- see RunSummary.timeline_events(). Colors are
# reused from above where a direct match exists (trade/rejected mirror the
# ok/failed status colors); shortage and disconnected are new roles specific
# to the timeline.
COLOR_SHORTAGE = "#eda100"
COLOR_DISCONNECTED = "#898781"
TIMELINE_COLORS = {
    "trade": COLOR_GOOD,
    "rejected": COLOR_CRITICAL,
    "shortage": COLOR_SHORTAGE,
    "disconnected": COLOR_DISCONNECTED,
}
TIMELINE_LABELS = {
    "trade": "Completed trades",
    "rejected": "Rejected requests",
    "shortage": "Shortages",
    "disconnected": "Disconnected periods",
}

# While a run is still going, main.py calls write_summary() again after every
# tick, overwriting this file on disk -- this is what makes an already-open
# browser tab pick that up: a plain page reload re-reads the file fresh, and
# is the one thing that still works for a file:// page. Once the run ends
# (run_ended is present), the render below drops this tag, so a finished
# dashboard just sits still.
LIVE_REFRESH_SECONDS = 3


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # A live log's last line can be mid-write; skip rather than abort.
                print(f"warning: skipping unparseable line {line_number} in {path}", file=sys.stderr)
    return events


class RunSummary:
    def __init__(self, events: list[dict[str, Any]]):
        by_event: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            by_event[event.get("event", "unknown")].append(event)

        self.run_starts = by_event["run_started"]
        self.run_ends = by_event["run_ended"]
        self.ticks = sorted(by_event["tick_snapshot"], key=lambda e: e["tick"])
        self.decisions = by_event["decision"]
        self.results = by_event["command_result"]
        self.transactions = by_event["transaction_settled"]
        self.offers_passed = by_event["offer_passed"]
        # Kept in file order (== chronological, since RunLog appends and
        # flushes every write) so disconnected_periods() can look at real
        # wall-clock gaps between events, not just tick numbers.
        self.events = events

        self.meta = self.run_starts[0] if self.run_starts else {}
        self.final = self.run_ends[-1] if self.run_ends else (self.ticks[-1] if self.ticks else {})
        self.incomplete = not self.run_ends

    @property
    def station_id(self) -> str:
        return self.meta.get("self_station_id", "unknown")

    @property
    def specialty(self) -> str:
        return self.meta.get("specialty", "unknown")

    def tick_series(self) -> dict[str, list]:
        return {
            # Chart x-position: a plain 0..n-1 index, not the raw tick number.
            # Some logs (e.g. the guided validation scenario in scenario.py)
            # take several snapshots that share one tick -- the exercise
            # advances by world_version/step, not by tick -- so plotting by
            # raw tick would collapse those points onto a single x and draw
            # no visible line at all. Index guarantees distinct, increasing
            # x-positions in every log; the real tick number is kept below
            # for the hover tooltip.
            "x": list(range(len(self.ticks))),
            "ticks": [t["tick"] for t in self.ticks],
            "water": [t["inventory"]["water"] for t in self.ticks],
            "food": [t["inventory"]["food"] for t in self.ticks],
            "components": [t["inventory"]["components"] for t in self.ticks],
            "health": [t["health"] for t in self.ticks],
            "budget_used": [t["budget_used"] for t in self.ticks],
            "budget_max": [t["budget_max"] for t in self.ticks],
        }

    def decision_counts(self) -> Counter:
        return Counter(d.get("kind", "unknown") for d in self.decisions)

    def decision_log(self) -> list[dict[str, Any]]:
        """Every decision the strategy made, in the order it made them, with
        its own stated reason, the exact terms it sent, and -- joined by
        ``request_id`` -- what the server did with it. This is the full
        history behind the aggregate ``decision_counts()`` bar chart: every
        trade offered or accepted, every gift, every advertisement, and why.
        """
        results_by_request_id = {r["request_id"]: r for r in self.results}
        rows = []
        for d in self.decisions:
            result = results_by_request_id.get(d.get("request_id"))
            sent = d.get("sent")
            rows.append({
                "tick": d.get("tick"),
                "kind": d.get("kind", "unknown"),
                "reason": d.get("reason", ""),
                "terms": _fmt_sent(sent),
                "counterparty": (sent or {}).get("recipient_id", ""),
                "outcome": _fmt_outcome(result),
                "outcome_ok": result.get("ok") if result else None,
            })
        return rows

    def command_outcome_counts(self) -> tuple[int, int, Counter]:
        ok = sum(1 for r in self.results if r["ok"])
        failed = len(self.results) - ok
        by_code = Counter(r["code"] for r in self.results if not r["ok"])
        return ok, failed, by_code

    def resource_flow(self) -> dict[str, dict[str, int]]:
        flow = {r: {"gave": 0, "received": 0} for r in RESOURCES}
        for tx in self.transactions:
            for r in RESOURCES:
                flow[r]["gave"] += tx["we_gave"].get(r, 0)
                flow[r]["received"] += tx["we_received"].get(r, 0)
        return flow

    def partner_volumes(self) -> list[tuple[str, int]]:
        totals: Counter = Counter()
        for tx in self.transactions:
            volume = sum(tx["we_gave"].values()) + sum(tx["we_received"].values())
            totals[tx["counterparty"]] += volume
        return totals.most_common()

    def shortage_periods(self) -> list[dict[str, Any]]:
        """Runs of consecutive observed ticks with any unmet upkeep.

        ``last_unmet_upkeep`` is only ever nonzero the tick production fell
        short of upkeep (``domain/station.py``), so this is the same signal
        the live dashboard flags as ``CRITICAL`` -- just grouped into spans
        instead of repeated once per tick, since a ten-tick shortage should
        read as one event, not ten.
        """
        periods: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for t in self.ticks:
            tick = t["tick"]
            unmet = t.get("last_unmet_upkeep") or {}
            short_resources = [r for r in RESOURCES if unmet.get(r, 0) > 0]
            if short_resources:
                if current is not None and tick == current["end"] + 1:
                    current["end"] = tick
                    current["resources"].update(short_resources)
                    current["max_streak"] = max(current["max_streak"], t.get("current_shortage_streak", 0))
                else:
                    if current is not None:
                        periods.append(current)
                    current = {
                        "start": tick, "end": tick,
                        "resources": set(short_resources),
                        "max_streak": t.get("current_shortage_streak", 0),
                    }
            elif current is not None:
                periods.append(current)
                current = None
        if current is not None:
            periods.append(current)
        return periods

    def disconnected_periods(self) -> list[dict[str, Any]]:
        """Gaps between consecutive events far longer than this run's pace.

        There is no explicit "disconnected" event in the schema (see
        ``docs/task7-structured-log-proposal.md``) -- a real disconnect just
        ends the run (``session.py``'s pump loop has no reconnect). This
        instead flags stretches where nothing got logged for much longer than
        usual: a slow command/sync round trip can let several ticks pass
        without a ``tick_snapshot`` (``strategy.run_trading`` only logs one
        when ``world.tick`` has changed by the time it next checks), which
        looks, from the log alone, exactly like a stall or dropped
        connection. The threshold adapts to the run's own median gap so a
        fast validation run (sub-millisecond gaps) doesn't get flagged on
        normal jitter, while a real multi-second-per-tick trading run still
        catches a genuine multi-tick stall.
        """
        timestamped = [e for e in self.events if e.get("wall_time") is not None]
        if len(timestamped) < 2:
            return []
        gaps = [
            timestamped[i + 1]["wall_time"] - timestamped[i]["wall_time"]
            for i in range(len(timestamped) - 1)
        ]
        threshold = max(statistics.median(gaps) * 5, 5.0)
        periods = []
        for i, gap in enumerate(gaps):
            if gap > threshold:
                before, after = timestamped[i], timestamped[i + 1]
                periods.append({
                    "start": before.get("tick"),
                    "end": after.get("tick"),
                    "gap_seconds": gap,
                })
        return periods

    def timeline_events(self) -> list[dict[str, Any]]:
        """Every notable thing that happened, across all event kinds, merged
        into one chronological list -- so a reader can follow the story of a
        run (what traded, what got rejected, when it went quiet, when supply
        ran short) without reading the raw log line by line.
        """
        events: list[dict[str, Any]] = []

        for tx in self.transactions:
            events.append({
                "start": tx.get("tick"), "end": tx.get("tick"), "type": "trade",
                "title": f"Trade settled with {tx['counterparty']}",
                "detail": f"we gave {_fmt_bundle(tx['we_gave'])}, we received {_fmt_bundle(tx['we_received'])}",
            })

        for row in self.decision_log():
            if row["outcome_ok"] is False:
                events.append({
                    "start": row["tick"], "end": row["tick"], "type": "rejected",
                    "title": f"Our {row['kind']} was rejected"
                             + (f" (to {row['counterparty']})" if row["counterparty"] else ""),
                    "detail": f"{row['reason']} -- {row['outcome']}",
                })

        for o in self.offers_passed:
            events.append({
                "start": o.get("tick"), "end": o.get("tick"), "type": "rejected",
                "title": f"Declined incoming offer from {o.get('counterparty', '?')}",
                "detail": (f"would give {_fmt_bundle(o.get('we_would_give', {}))}, "
                           f"would receive {_fmt_bundle(o.get('we_would_receive', {}))} "
                           f"-- {o.get('reason', '')}"),
            })

        for period in self.shortage_periods():
            resources = ", ".join(sorted(period["resources"]))
            events.append({
                "start": period["start"], "end": period["end"], "type": "shortage",
                "title": f"Shortage: unmet upkeep for {resources}",
                "detail": f"shortage streak reached {period['max_streak']}",
            })

        for period in self.disconnected_periods():
            events.append({
                "start": period["start"], "end": period["end"], "type": "disconnected",
                "title": "Possible stall or disconnect",
                "detail": f"{period['gap_seconds']:.1f}s with no new log events "
                          f"(last seen at tick {period['start']}, resumed at tick {period['end']})",
            })

        events.sort(key=lambda e: (e["start"] if e["start"] is not None else -1,
                                    e["end"] if e["end"] is not None else -1))
        return events


def svg_line_chart(chart_id: str, xs: list[int], series: dict[str, tuple[list[int], str]],
                    width: int = 720, height: int = 220, pad: int = 36) -> str:
    """A multi-series line chart with a hover crosshair (see chart.js in the page)."""
    if not xs:
        return '<p class="empty">No tick data recorded yet.</p>'
    all_values = [v for values, _ in series.values() for v in values]
    y_min, y_max = min(0, min(all_values)), max(all_values) or 1
    x_min, x_max = min(xs), max(xs) or 1

    def sx(x: int) -> float:
        return pad + (x - x_min) / max(1, x_max - x_min) * (width - 2 * pad)

    def sy(y: int) -> float:
        return height - pad - (y - y_min) / max(1, y_max - y_min) * (height - 2 * pad)

    paths = []
    for name, (values, color) in series.items():
        points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, values))
        paths.append(f'<polyline points="{points}" fill="none" stroke="{color}" '
                      f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" data-series="{name}"/>')
    gridlines = "".join(
        f'<line x1="{pad}" y1="{sy(y):.1f}" x2="{width - pad}" y2="{sy(y):.1f}" class="gridline"/>'
        for y in _nice_ticks(y_min, y_max)
    )
    y_labels = "".join(
        f'<text x="{pad - 6}" y="{sy(y):.1f}" class="axis-label" text-anchor="end" dominant-baseline="middle">{y}</text>'
        for y in _nice_ticks(y_min, y_max)
    )
    return f'''
<div class="chart-wrap">
  <svg class="chart" id="{chart_id}" viewBox="0 0 {width} {height}" data-xmin="{x_min}" data-xmax="{x_max}"
       data-pad="{pad}" data-width="{width}">
    {gridlines}
    {y_labels}
    {"".join(paths)}
    <line class="crosshair" x1="0" y1="{pad}" x2="0" y2="{height - pad}" visibility="hidden"/>
  </svg>
  <div class="tooltip" id="{chart_id}-tooltip" hidden></div>
</div>'''


def _nice_ticks(low: float, high: float, count: int = 4) -> list[int]:
    if high <= low:
        return [int(low)]
    step = max(1, round((high - low) / count))
    return list(range(int(low), int(high) + step, step))


def svg_bar_chart(bars: list[tuple[str, int, str]], width: int = 720, height_per_bar: int = 32) -> str:
    """A horizontal bar chart; native <title> gives a free hover tooltip per bar."""
    if not bars:
        return '<p class="empty">No data recorded yet.</p>'
    max_value = max(v for _, v, _ in bars) or 1
    pad_left = 140
    height = len(bars) * height_per_bar + 20
    rows = []
    for i, (label, value, color) in enumerate(bars):
        y = i * height_per_bar + 10
        bar_width = (value / max_value) * (width - pad_left - 60)
        rows.append(f'''
    <text x="{pad_left - 10}" y="{y + height_per_bar / 2}" text-anchor="end" dominant-baseline="middle" class="bar-label">{label}</text>
    <rect x="{pad_left}" y="{y + 4}" width="{max(2, bar_width):.1f}" height="{height_per_bar - 12}" fill="{color}" rx="3">
      <title>{label}: {value}</title>
    </rect>
    <text x="{pad_left + bar_width + 8}" y="{y + height_per_bar / 2}" dominant-baseline="middle" class="bar-value">{value}</text>''')
    return f'<svg class="chart" viewBox="0 0 {width} {height}">{"".join(rows)}</svg>'


def render_timeline(events: list[dict[str, Any]]) -> str:
    """A vertical, filterable timeline of ``RunSummary.timeline_events()``.

    Plain divs, not a table -- entries span a tick range (a shortage or
    disconnected period) as often as a single tick (a trade or rejection),
    which a table row doesn't represent well. ``data-type``/``data-filter``
    mirror the pattern the existing tables use for their filter inputs
    (see JS), so the same filtering approach is applied here too.
    """
    if not events:
        return '<p class="empty">No timeline events recorded.</p>'
    rows = []
    for e in events:
        tick_label = f"tick {e['start']}" if e["start"] == e["end"] else f"ticks {e['start']}&ndash;{e['end']}"
        filter_text = f"{e['type']} {e['title']} {e['detail']}"
        rows.append(f'''
    <div class="timeline-item timeline-{e['type']}" data-type="{e['type']}" data-filter="{filter_text}">
      <div class="timeline-tick">{tick_label}</div>
      <div class="timeline-body">
        <div class="timeline-title">{e['title']}</div>
        <div class="timeline-detail">{e['detail']}</div>
      </div>
    </div>''')
    return "".join(rows)


def render_html(summary: RunSummary) -> str:
    series = summary.tick_series()
    inventory_chart = svg_line_chart(
        "inventory-chart", series["x"],
        {"water": (series["water"], COLOR_WATER),
         "food": (series["food"], COLOR_FOOD),
         "components": (series["components"], COLOR_COMPONENTS)},
    )
    health_chart = svg_line_chart(
        "health-chart", series["x"], {"health": (series["health"], COLOR_HEALTH)}, height=140,
    )

    counts = summary.decision_counts()
    decision_bars = [
        (kind, counts.get(kind, 0), DECISION_KIND_COLORS.get(kind, "#898781"))
        for kind in DECISION_KIND_ORDER if counts.get(kind, 0) > 0
    ]
    for kind, count in counts.items():
        if kind not in DECISION_KIND_ORDER:
            decision_bars.append((kind, count, "#898781"))
    decisions_chart = svg_bar_chart(decision_bars, height_per_bar=28)

    ok, failed, by_code = summary.command_outcome_counts()
    outcome_bars = [("ok", ok, COLOR_GOOD)] + ([("failed", failed, COLOR_CRITICAL)] if failed else [])
    outcome_chart = svg_bar_chart(outcome_bars, height_per_bar=28)
    failure_rows = "".join(
        f"<tr><td>{code}</td><td>{count}</td></tr>" for code, count in by_code.most_common()
    )

    flow = summary.resource_flow()
    resource_color = {"water": COLOR_WATER, "food": COLOR_FOOD, "components": COLOR_COMPONENTS}
    flow_tiles = "".join(f'''
    <div class="tile">
      <div class="tile-label" style="color:{resource_color[r]}">{r}</div>
      <div class="tile-value">+{flow[r]["received"]} / -{flow[r]["gave"]}</div>
      <div class="tile-sub">received / gave</div>
    </div>''' for r in RESOURCES)

    partner_rows = "".join(
        f"<tr><td>{station}</td><td>{volume}</td></tr>" for station, volume in summary.partner_volumes()
    )

    tx_rows = "".join(f'''
    <tr data-filter="{tx['counterparty']}">
      <td>{tx.get('tick', '')}</td>
      <td>{tx['counterparty']}</td>
      <td>{_fmt_bundle(tx['we_gave'])}</td>
      <td>{_fmt_bundle(tx['we_received'])}</td>
    </tr>''' for tx in summary.transactions)

    def decision_row_html(row: dict) -> str:
        outcome_color = {True: COLOR_GOOD, False: COLOR_CRITICAL, None: "var(--muted)"}[row["outcome_ok"]]
        filter_text = f"{row['kind']} {row['counterparty']} {row['reason']}"
        return f'''
    <tr data-filter="{filter_text}">
      <td>{row['tick']}</td>
      <td>{row['kind']}</td>
      <td>{row['reason']}</td>
      <td>{row['terms']}</td>
      <td style="color:{outcome_color}">{row['outcome']}</td>
    </tr>'''

    decision_rows = "".join(decision_row_html(row) for row in summary.decision_log())

    timeline_events = summary.timeline_events()
    timeline_chart = svg_line_chart(
        "timeline-inventory-chart", series["x"],
        {"water": (series["water"], COLOR_WATER),
         "food": (series["food"], COLOR_FOOD),
         "components": (series["components"], COLOR_COMPONENTS)},
        height=160,
    )
    timeline_counts = Counter(e["type"] for e in timeline_events)
    timeline_toggles = "".join(f'''
      <button type="button" class="chip chip-{t}" data-type="{t}" aria-pressed="true">
        <i style="background:{TIMELINE_COLORS[t]}"></i>{TIMELINE_LABELS[t]} ({timeline_counts.get(t, 0)})
      </button>''' for t in TIMELINE_LABELS)
    timeline_rows = render_timeline(timeline_events)

    final = summary.final
    refresh_tag = ""
    if summary.incomplete:
        refresh_tag = f'<meta http-equiv="refresh" content="{LIVE_REFRESH_SECONDS}">'
        status_note = (
            f'<p class="note">&#9679; Live -- run in progress, showing the latest tick observed. '
            f"This page reloads itself every {LIVE_REFRESH_SECONDS}s; it stops once the run ends.</p>"
        )
    else:
        status_note = ""

    chart_payload = json.dumps({
        "inventory-chart": {
            "ticks": series["x"],
            "labels": series["ticks"],
            "series": {"water": series["water"], "food": series["food"], "components": series["components"]},
            "colors": {"water": COLOR_WATER, "food": COLOR_FOOD, "components": COLOR_COMPONENTS},
        },
        "health-chart": {
            "ticks": series["x"],
            "labels": series["ticks"],
            "series": {"health": series["health"]},
            "colors": {"health": COLOR_HEALTH},
        },
        "timeline-inventory-chart": {
            "ticks": series["x"],
            "labels": series["ticks"],
            "series": {"water": series["water"], "food": series["food"], "components": series["components"]},
            "colors": {"water": COLOR_WATER, "food": COLOR_FOOD, "components": COLOR_COMPONENTS},
        },
    }).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Run summary: {summary.station_id}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<style>
{CSS}
</style>
</head>
<body>
<main class="viz-root">
  <header>
    <h1>Run summary &mdash; {summary.station_id}</h1>
    <p class="subtitle">specialty {summary.specialty} &middot; run {summary.meta.get('run_id', 'unknown')}
      &middot; final tick {final.get('tick', '?')} &middot; {"failed once" if final.get('failed_once') else "no failures"}</p>
    {status_note}
  </header>

  <div class="tabs" role="tablist">
    <button type="button" class="tab-button active" data-tab="overview" role="tab" aria-selected="true">Overview</button>
    <button type="button" class="tab-button" data-tab="timeline" role="tab" aria-selected="false">Timeline</button>
  </div>

  <div class="tab-panel active" id="tab-overview" role="tabpanel">
  <section class="tiles">
    <div class="tile"><div class="tile-label">Final health</div><div class="tile-value">{final.get('health', '?')}</div></div>
    <div class="tile"><div class="tile-label">Final inventory</div><div class="tile-value">{_fmt_bundle(final.get('final_inventory', {}))}</div></div>
    <div class="tile"><div class="tile-label">Decisions made</div><div class="tile-value">{len(summary.decisions)}</div></div>
    <div class="tile"><div class="tile-label">Commands ok / failed</div><div class="tile-value">{ok} / {failed}</div></div>
    <div class="tile"><div class="tile-label">Transactions settled</div><div class="tile-value">{len(summary.transactions)}</div></div>
  </section>

  <section>
    <h2>Inventory over time</h2>
    <div class="legend">
      <span><i style="background:{COLOR_WATER}"></i>water</span>
      <span><i style="background:{COLOR_FOOD}"></i>food</span>
      <span><i style="background:{COLOR_COMPONENTS}"></i>components</span>
    </div>
    {inventory_chart}
  </section>

  <section>
    <h2>Health over time</h2>
    {health_chart}
  </section>

  <section class="grid-2">
    <div>
      <h2>Decisions by kind</h2>
      {decisions_chart}
    </div>
    <div>
      <h2>Command outcomes</h2>
      {outcome_chart}
      {"<table class='data'><thead><tr><th>failure code</th><th>count</th></tr></thead><tbody>" + failure_rows + "</tbody></table>" if failure_rows else ""}
    </div>
  </section>

  <section>
    <h2>Decision log</h2>
    <p class="subtitle">Every trade offered or accepted, every gift, and every advertisement this
      station made, in order, with the reason it decided that and what the server did with it.</p>
    <input type="text" id="decision-filter" data-target="decision-table"
           placeholder="Filter by kind, partner, or reason..." class="filter">
    <table class="data" id="decision-table">
      <thead><tr><th>tick</th><th>kind</th><th>reason</th><th>terms</th><th>outcome</th></tr></thead>
      <tbody>{decision_rows or '<tr><td colspan="5">No decisions recorded yet.</td></tr>'}</tbody>
    </table>
  </section>

  <section>
    <h2>Resource flow (settled trades)</h2>
    <div class="tiles">{flow_tiles}</div>
  </section>

  <section class="grid-2">
    <div>
      <h2>Trade volume by partner</h2>
      <table class="data"><thead><tr><th>partner</th><th>units exchanged</th></tr></thead>
      <tbody>{partner_rows or '<tr><td colspan="2">No settled trades yet.</td></tr>'}</tbody></table>
    </div>
    <div>
      <h2>Settled transactions</h2>
      <input type="text" id="tx-filter" data-target="tx-table" placeholder="Filter by partner id..." class="filter">
      <table class="data" id="tx-table"><thead><tr><th>tick</th><th>partner</th><th>we gave</th><th>we received</th></tr></thead>
      <tbody>{tx_rows or '<tr><td colspan="4">No settled trades yet.</td></tr>'}</tbody></table>
    </div>
  </section>
  </div>

  <div class="tab-panel" id="tab-timeline" role="tabpanel">
  <section>
    <h2>Resource history</h2>
    <p class="subtitle">Same inventory series as the Overview tab, kept here so the story below has
      the resource trend right next to it.</p>
    <div class="legend">
      <span><i style="background:{COLOR_WATER}"></i>water</span>
      <span><i style="background:{COLOR_FOOD}"></i>food</span>
      <span><i style="background:{COLOR_COMPONENTS}"></i>components</span>
    </div>
    {timeline_chart}
  </section>

  <section>
    <h2>Run timeline</h2>
    <p class="subtitle">Completed trades, rejected requests, shortages, and suspected disconnected
      periods, merged into one chronological view -- so what happened during this run is visible
      without reading the raw log line by line.</p>
    <div class="chip-row">{timeline_toggles}</div>
    <input type="text" id="timeline-filter" placeholder="Filter by type, partner, or reason..." class="filter">
    <div class="timeline" id="timeline-list">{timeline_rows}</div>
  </section>
  </div>
</main>

<script id="chart-data" type="application/json">{chart_payload}</script>
<script>
{JS}
</script>
</body>
</html>
"""


def _fmt_bundle(bundle: dict) -> str:
    if not bundle:
        return "?"
    return f"{bundle.get('water', 0)}w / {bundle.get('food', 0)}f / {bundle.get('components', 0)}c"


def _fmt_sent(sent: dict | None) -> str:
    """The exact terms of a decision's outgoing command, for the decision log.

    ``sent`` is only present when the caller passed the wire message to
    ``structured_log.RunLog.decision()``; every current call site does, but
    this falls back to "-" (rather than raising) for any log written by an
    older client version, or a future caller that only has a reason.
    """
    if not sent:
        return "-"
    command = sent.get("command")
    if command == "offer":
        return (f"give {_fmt_bundle(sent.get('give', {}))} to {sent.get('recipient_id', '?')}, "
                f"receive {_fmt_bundle(sent.get('receive', {}))} (expires tick {sent.get('expires_tick', '?')})")
    if command == "accept":
        return f"accept offer {sent.get('offer_id', '?')}"
    if command == "withdraw":
        return f"withdraw offer {sent.get('object_id', '?')}"
    if command == "advertise":
        selling = ", ".join(sent.get("selling", [])) or "nothing"
        seeking = ", ".join(sent.get("seeking", [])) or "nothing"
        return f"selling {selling}; seeking {seeking} (expires tick {sent.get('expires_tick', '?')})"
    return command or "-"


def _fmt_outcome(result: dict | None) -> str:
    """The server's answer to a decision, joined in by ``request_id``.

    ``None`` means no matching ``command_result`` was logged at all -- the
    run ended (a timeout, a crash) before the server replied to this one.
    """
    if result is None:
        return "pending"
    if result.get("ok"):
        ref = result.get("transaction_id") or result.get("object_id")
        if not ref:
            return "ok"
        # IDs are full-length UUIDs in practice -- too wide for a table
        # column. Shown short with the full value on hover, not in the
        # filterable text, since nobody searches a decision log by UUID.
        short_ref = ref if len(ref) <= 12 else f"{ref[:8]}&hellip;"
        return f'ok (<span title="{ref}">{short_ref}</span>)'
    retry = result.get("retry_after_tick")
    retry_note = f", retry after tick {retry}" if retry else ""
    return f"failed: {result.get('code')}{retry_note}"


CSS = """
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --gridline: #e1e0d9;
  --border: rgba(11,11,11,0.10);
}
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark; --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff;
          --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a; --border: rgba(255,255,255,0.10); }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text-primary);
       font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 0 0 12px; }
.subtitle, .note { color: var(--text-secondary); font-size: 0.9rem; }
.note { padding: 8px 12px; background: var(--surface-1); border: 1px solid var(--border); border-radius: 6px; }
section { margin-top: 32px; padding: 16px; background: var(--surface-1); border: 1px solid var(--border);
          border-radius: 10px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid-2 > div { background: var(--surface-1); }
@media (max-width: 720px) { .grid-2 { grid-template-columns: 1fr; } }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.tile { padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
.tile-label { color: var(--text-secondary); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
.tile-value { font-size: 1.3rem; font-variant-numeric: tabular-nums; margin-top: 2px; }
.tile-sub { color: var(--muted); font-size: 0.75rem; }
.legend { display: flex; gap: 16px; margin-bottom: 8px; font-size: 0.85rem; color: var(--text-secondary); }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; }
.chart-wrap { position: relative; }
.chart { width: 100%; height: auto; }
.gridline { stroke: var(--gridline); stroke-width: 1; }
.axis-label, .bar-label, .bar-value { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.bar-value { fill: var(--text-primary); }
.crosshair { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }
.tooltip { position: absolute; pointer-events: none; background: var(--text-primary); color: var(--surface-1);
           font-size: 0.8rem; padding: 6px 8px; border-radius: 6px; transform: translate(-50%, -110%);
           white-space: nowrap; }
table.data { width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 8px; }
table.data th, table.data td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--gridline); }
table.data th { color: var(--text-secondary); font-weight: 600; }
.filter { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px;
          background: var(--page); color: var(--text-primary); margin-bottom: 4px; }
.empty { color: var(--muted); font-size: 0.9rem; }

.tabs { display: flex; gap: 4px; margin-top: 20px; border-bottom: 1px solid var(--border); }
.tab-button { padding: 8px 16px; border: none; background: none; color: var(--text-secondary);
              font-size: 0.9rem; font-family: inherit; cursor: pointer; border-bottom: 2px solid transparent;
              margin-bottom: -1px; }
.tab-button.active { color: var(--text-primary); font-weight: 600; border-bottom-color: var(--text-primary); }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

.chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px;
        border: 1px solid var(--border); background: var(--page); color: var(--text-primary);
        font-size: 0.8rem; font-family: inherit; cursor: pointer; }
.chip i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
.chip[aria-pressed="false"] { color: var(--muted); opacity: 0.5; }

.timeline { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.timeline-item { display: flex; gap: 14px; padding: 10px 12px; background: var(--page);
                 border: 1px solid var(--border); border-left: 4px solid var(--muted); border-radius: 6px; }
.timeline-trade { border-left-color: #0ca30c; }
.timeline-rejected { border-left-color: #d03b3b; }
.timeline-shortage { border-left-color: #eda100; }
.timeline-disconnected { border-left-color: #898781; }
.timeline-tick { flex: 0 0 auto; min-width: 90px; color: var(--muted); font-size: 0.78rem;
                 font-variant-numeric: tabular-nums; padding-top: 1px; }
.timeline-title { font-size: 0.88rem; font-weight: 600; }
.timeline-detail { color: var(--text-secondary); font-size: 0.82rem; margin-top: 2px; }
"""

JS = """
(function () {
  const data = JSON.parse(document.getElementById('chart-data').textContent);

  function attachHover(svgId) {
    const cfg = data[svgId];
    if (!cfg || cfg.ticks.length === 0) return;
    const svg = document.getElementById(svgId);
    const tooltip = document.getElementById(svgId + '-tooltip');
    const crosshair = svg.querySelector('.crosshair');
    const viewBox = svg.viewBox.baseVal;
    const pad = Number(svg.dataset.pad);
    const width = Number(svg.dataset.width);
    const xmin = Number(svg.dataset.xmin), xmax = Number(svg.dataset.xmax);

    function tickAt(clientX) {
      const rect = svg.getBoundingClientRect();
      const fracX = (clientX - rect.left) / rect.width;
      const vbX = fracX * viewBox.width;
      const frac = Math.min(1, Math.max(0, (vbX - pad) / (width - 2 * pad)));
      const approxTick = xmin + frac * (xmax - xmin);
      let closest = 0, bestDist = Infinity;
      cfg.ticks.forEach((t, i) => {
        const dist = Math.abs(t - approxTick);
        if (dist < bestDist) { bestDist = dist; closest = i; }
      });
      return closest;
    }

    svg.addEventListener('mousemove', (event) => {
      const i = tickAt(event.clientX);
      const x = cfg.ticks[i];
      const label = cfg.labels ? cfg.labels[i] : x;
      const frac = (x - xmin) / Math.max(1, xmax - xmin);
      const svgX = pad + frac * (width - 2 * pad);
      crosshair.setAttribute('x1', svgX);
      crosshair.setAttribute('x2', svgX);
      crosshair.setAttribute('visibility', 'visible');

      const lines = Object.keys(cfg.series).map((name) => `${name}: ${cfg.series[name][i]}`);
      tooltip.innerHTML = `tick ${label}<br>` + lines.join('<br>');
      tooltip.hidden = false;
      const rect = svg.getBoundingClientRect();
      const wrap = svg.parentElement.getBoundingClientRect();
      tooltip.style.left = (rect.left - wrap.left + (svgX / viewBox.width) * rect.width) + 'px';
      tooltip.style.top = '0px';
    });
    svg.addEventListener('mouseleave', () => {
      crosshair.setAttribute('visibility', 'hidden');
      tooltip.hidden = true;
    });
  }

  Object.keys(data).forEach(attachHover);

  document.querySelectorAll('.filter[data-target]').forEach((input) => {
    const rows = document.querySelectorAll('#' + input.dataset.target + ' tbody tr');
    input.addEventListener('input', () => {
      const needle = input.value.trim().toLowerCase();
      rows.forEach((row) => {
        const haystack = (row.dataset.filter || '').toLowerCase();
        row.style.display = !needle || haystack.includes(needle) ? '' : 'none';
      });
    });
  });

  document.querySelectorAll('.tab-button').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab-button').forEach((b) => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      button.classList.add('active');
      button.setAttribute('aria-selected', 'true');
      document.getElementById('tab-' + button.dataset.tab).classList.add('active');
    });
  });

  const timelineList = document.getElementById('timeline-list');
  if (timelineList) {
    const items = Array.from(timelineList.querySelectorAll('.timeline-item'));
    const activeTypes = new Set(Array.from(document.querySelectorAll('.chip')).map((c) => c.dataset.type));
    const timelineFilter = document.getElementById('timeline-filter');

    function applyTimelineFilters() {
      const needle = (timelineFilter ? timelineFilter.value : '').trim().toLowerCase();
      items.forEach((item) => {
        const matchesType = activeTypes.has(item.dataset.type);
        const matchesText = !needle || (item.dataset.filter || '').toLowerCase().includes(needle);
        item.style.display = matchesType && matchesText ? '' : 'none';
      });
    }

    document.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        const pressed = chip.getAttribute('aria-pressed') === 'true';
        chip.setAttribute('aria-pressed', pressed ? 'false' : 'true');
        if (pressed) activeTypes.delete(chip.dataset.type);
        else activeTypes.add(chip.dataset.type);
        applyTimelineFilters();
      });
    });
    if (timelineFilter) timelineFilter.addEventListener('input', applyTimelineFilters);
  }
})();
"""


def write_summary(log_path: str | Path, out_path: str | Path | None = None) -> Path:
    """Read ``log_path`` and write its HTML summary, returning the output path.

    Renders even a log with zero events (an empty or freshly-opened file) --
    a mostly-empty dashboard is still useful feedback (e.g. a run that failed
    before logging anything) -- so this never raises on a "boring" log. It
    still raises if ``log_path`` itself doesn't exist.
    """
    log_path = Path(log_path)
    summary = RunSummary(load_events(log_path))
    html = render_html(summary)

    out_path = Path(out_path) if out_path else log_path.with_name(log_path.stem + "-summary.html")
    out_path.write_text(html, encoding="utf-8")
    return out_path
