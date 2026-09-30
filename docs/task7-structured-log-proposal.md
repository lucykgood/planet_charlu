# Task 7 proposal: a structured run log for Bazaar clients

Scaffolding for the P02/P04 discussion on Task 7: a structured log format any
client can produce, a run summary worth generating from it, and a script that
generates that summary. This branch (`task7/structured-run-log`) wires a
working first draft into Planet CharLu so we have something concrete to react
to, not just a spec on paper.

There end up being two complementary pieces, aimed at two different readers:

1. **A human-readable status dashboard, on by default.** Every tick (in trade
   mode) and after every step (in the validator's ten-step exercise), a
   multi-line block logs -- current epoch, reserves vs. upkeep, our own
   pending offers/advertisements, offers open to us, and recent settled
   trades -- via the normal logger, no flag required. This is
   `format_status_report()` in `logging_utils.py`; see "The live dashboard"
   below.
2. **A machine-readable JSON export, on by default too, feeding an automatic
   HTML dashboard.** This is what the rest of this document is about: a
   schema meant to be *replayed* by a program (the summary generator, a
   future cross-team comparison tool, a grader), not read directly. Every
   run/simulation writes one to an auto-named file under `runs/`, in both
   trade and validation mode, and `main.py` renders the HTML dashboard from
   it automatically the moment the run ends -- success or failure -- so
   there's always something to open afterward without a separate step.

## The live dashboard

No flag needed. In trade mode, `run_trading()` logs a block like this each
time the tick changes; in validation mode, `run_sample_scenario()` logs the
same block after each of the ten steps -- so the bundled local validator
(`./scripts/run_validator.sh`) is a real, no-live-match way to see it work:

```text
----------------------------------------------------------------------
tick 5 | running | station=P01 specialty=water health=100
reserves: water=30 (reserve 3, ok) | food=2 (reserve 3, CRITICAL) | components=5 (reserve 3, ok)
pending actions: none
open offers to us (2):
  <- offer from P02: we'd give 4 water, we'd receive 4 food (expires tick 10)
  <- offer from P05: we'd give 3 water, we'd receive 1 food (expires tick 10)
recent trades: none yet
passed on 1 incoming offer(s):
  x offer from P05: we'd give 3 water, we'd receive 1 food -- unfavorable: would give more than we receive
trading: accept favorable-or-equal trade for a resource we need (offer good)
  -> ok
```

The leading horizontal rule (`TICK_SEPARATOR`) makes each tick's/step's block
easy to pick out when scrolling past the decision/result lines the previous
one left behind. "Pending actions" is our own outstanding offers and active
advertisements -- proposals we've sent that we're waiting on a response to.
"Open offers to us" is the other direction: offers a partner sent that we
could still act on -- and the "passed on ..." line right after the decision
says which of those it turned down and why (`format_offer_review()`), so the
one it accepted isn't the only one visible; only its own dedup key ever
appears more than once. Reserves flag `CRITICAL` the same way `strategy.py`
does internally (inventory below three ticks of upkeep), so a shortfall is
visible before it becomes a health problem. Trades are shown from *our*
perspective (`gave`/`received`) rather than the wire's raw proposer/recipient
framing, via `Offer.relative_to()` / `Transaction.relative_to()`
(`domain/offers.py`, `domain/transactions.py`) -- the same normalization the
JSON export below uses, so both views agree. The action line itself
(`format_sent_terms()`) shows the exact amounts sent, not just the reason
sentence, and the result line (`format_result_extra()`) shows the
object/transaction ID the server actually created on success. Enum-style wire
values are humanized throughout (`PHASE_RUNNING` -> `running`,
`RESULT_CODE_RATE_LIMITED` -> `rate limited`); pass `--verbose`/`-v` to also
see the raw per-message protocol trace at DEBUG level if you need it.

The JSON export covers both modes too: `RunLog.decision()`/`command_result()`
take plain `key`/`reason`/`request_id` fields rather than a `strategy.Decision`
object, so `scenario.py`'s ten fixed steps (which call `codec.build_*`
directly, with no `Decision` in sight) log through the exact same methods
`run_trading()` uses -- one schema, one summary renderer, regardless of mode.

## Why a separate machine-readable log from the existing one

`logging_utils.py` already gives us a readable text log (the dashboard above,
plus one-line-per-decision entries like `trading: accept favorable-or-equal
trade...`), and that stays -- it's what you tail live during a run. It is not,
however, meant to be *parsed*: line format and wording can change, and there is
no way to tell "this line is a decision" from "this line is a result" without
regexing prose.

The structured log is a second, parallel artifact whose only job is to be
replayed by a program after the run: a summary generator, a dashboard, a
cross-team diff of two runs, a grader's checker. It is newline-delimited JSON
(JSON Lines / NDJSON) -- one self-contained JSON object per line -- rather than
one JSON array, because:

- It can be appended to and tailed like a normal log file; the writer never
  has to rewrite the whole file to add one event, and a reader can start
  summarizing before the run ends.
- A truncated last line (process killed mid-write) only costs that one event,
  not the whole file. `run_summary.py`'s loader already skips an unparseable
  trailing line rather than aborting.
- Every standard tool (`jq`, `pandas.read_json(lines=True)`, `grep`) already
  understands it.

## Event schema

Every line is `{"schema_version": 1, "event": "<name>", "wall_time": <unix
seconds>, "tick": <int|null>, ...event-specific fields}`. `wall_time` is real
clock time (useful for correlating our log against a teammate's or the
server's); `tick` is game time. Resource bundles are always `{"water": int,
"food": int, "components": int}`, matching `domain/resources.py`'s `Bundle`.

| event | when it's written | key fields |
|---|---|---|
| `run_started` | once, at the first snapshot | `run_id`, `mode`, `self_station_id`, `specialty`, `duration_ticks`, `max_request_records_per_station`, `starting_inventory`, `upkeep_per_tick` |
| `tick_snapshot` | once per tick actually observed | `phase`, `health`, `inventory`, `last_production`, `last_unmet_upkeep`, `current_shortage_streak`, `open_offers_from_me`, `open_offers_to_me`, `active_advertisements`, `budget_used`, `budget_max` |
| `offers_open` | once per tick, only when something is actually open | `outgoing` / `incoming`: lists of `{offer_id, counterparty, we_give, we_receive, expires_tick}`, our own perspective -- what the client *knew* was on the board |
| `offer_passed` | once per incoming offer considered and not accepted | `offer_id`, `counterparty`, `we_would_give`, `we_would_receive`, `reason` (e.g. `"unfavorable: would give more than we receive"`, `"would breach the upkeep reserve"`) |
| `decision` | whenever the strategy (or a scenario step) picks an action | `key` (its own dedup key), `kind` (`withdraw` / `accept` / `advertise` / `seek_trade` / `gift`), `reason` (free text), `request_id`, `sent` (the exact wire command when the caller has it: give/receive/recipient/offer_id/etc.) |
| `command_result` | when the server replies to a sent command | `key`, `request_id`, `ok`, `code` (e.g. `RESULT_CODE_OK`), `object_id`, `transaction_id`, `retry_after_tick` |
| `transaction_settled` | once per settled transaction that involves us, the first time it appears in a snapshot | `transaction_id`, `offer_id`, `counterparty`, `we_gave`, `we_received` (already resolved to *our* perspective, unlike the wire `Transaction`'s proposer/recipient framing) |
| `run_ended` | once, when the run loop returns or a command/sync times out | `reason`, `phase`, `health`, `failed_once`, `final_inventory`, `transaction_count` |

Together, for any given offer, this reconstructs the full loop: `offers_open` (what it knew was there) -> `offer_passed` or `decision`+`sent` (what it decided, why, and exactly what it sent) -> `command_result` (`object_id`/`transaction_id` link it to what the server actually created) -> `transaction_settled` (what settled, from `offer_id`). `offer_passed` in particular closes a real gap: without it, only the one offer eventually accepted (if any) ever left a trace -- every offer considered and declined for being unfavorable, unaffordable, or simply because the budget was already spent would otherwise vanish the moment a newer snapshot replaced it.

Design choices worth flagging for the group:

- **`kind` on `decision` is derived, not raw.** The strategy's own dedup key
  (`Decision.key` in `strategy.py`) uses `partner:<station_id>` for two
  different actions -- seeking a needed resource and gifting surplus specialty
  -- because both only need to dedup per-partner. A log consumer shouldn't
  have to know that and pattern-match `reason` text itself, so
  `RunLog.decision()` does that split once, centrally
  (`src/planet_charlu/structured_log.py`).
- **Transactions (and offers) are perspective-normalized.** The wire
  `Transaction`/`Offer` are `give`/`receive` from the *proposer's* perspective;
  a naive log would force every reader to re-derive "did we give or receive
  this" by comparing station IDs. `Transaction.relative_to()` and
  `Offer.relative_to()` (`domain/transactions.py`, `domain/offers.py`) do that
  once, and both the JSON export and the live dashboard above call the same
  methods -- so `we_gave`/`we_received` in the log and "gave"/"received" in
  the dashboard always agree.
- **Idempotent by construction.** Snapshots are complete, not incremental
  (`ARCHITECTURE.md`), so the same settled transaction reappears in every
  later snapshot. `RunLog` tracks seen transaction IDs internally so
  `transactions_settled()` can be called every tick without double-logging.
- **A tolerant reader, not just a careful writer.** `run_summary.py` never
  assumes a well-formed, complete file: a missing `run_ended` (client still
  running, or crashed) degrades to "here's the latest tick we saw," and even
  zero events renders a (mostly empty) page instead of raising, and
  a malformed trailing line is skipped with a warning instead of aborting the
  whole summary.
- **`offer_passed` is a side channel on the strategy, not a return-type
  change.** `SelfSufficientStrategy.choose()` returns at most one `Decision`,
  and a lot of code (tests included) depends on that. Recording *every*
  incoming offer it looked at -- not just the one it acted on -- without
  touching that signature meant adding `self.last_offer_review: list[(Offer,
  reason)]`, repopulated at the top of every `choose()` call; `run_trading()`
  reads it right after calling `choose()`, logging everything except the
  `'accepted'` entry (that one's already covered by `decision` +
  `command_result` + `transaction_settled`). It's instrumentation added
  around the existing decision loop, not a change to what the loop decides.

## Producing it

`src/planet_charlu/structured_log.py` defines `RunLog`, an append-only JSON
Lines writer (one method per event above, flushing after every write). It's
wired into both `strategy.run_trading()` and `scenario.run_sample_scenario()`
behind an optional `run_log` parameter (`None` disables it entirely -- used by
tests that don't care about logging). `main.py` supplies one for every real
run: `config.py` auto-names a path under `runs/` (`runs/<station_id>-<UTC
timestamp>.jsonl`) unless you override it with `--run-log <path>` or
`BAZAAR_RUN_LOG`:

```bash
python -m planet_charlu.main --mode trade \
  --ws-url wss://spaceport.edneo.com/ws --station-id P01
# -> runs/P01-20260930T091500.jsonl
```

`RunLog.open()` creates the `runs/` directory itself if it doesn't exist yet
(`runs/` is gitignored, like the other local run output). This is
intentionally a small integration: one file, one parameter threaded through
each policy path, no change to what gets sent over the wire or how decisions
are made.

## Producing the summary

This isn't only an after-the-fact report: `RunLog.refresh_html()` re-renders
it from the log-so-far after every tick (`run_trading()`) or step
(`run_sample_scenario()`), so a page already open in a browser can watch a run
happen -- `render_html()` embeds `<meta http-equiv="refresh" content="3">`
whenever `run_ended` hasn't been written yet (`summary.incomplete`), so an
open tab reloads itself every `LIVE_REFRESH_SECONDS` while the run is live. A
plain page reload is deliberate, not a limitation: this is a `file://` page
with no server behind it, and `fetch`/`XHR`/WebSockets don't work against a
local file in most browsers, so a full reload is the one thing guaranteed to
re-read the file from disk. `RunLog.run_ended()` always calls
`refresh_html()` itself as its last step -- regardless of which caller
forgets to -- so the tag reliably disappears and the final render is never
left stuck mid-refresh:

```
HTML run summary written to runs/P01-20260930T091500-summary.html
```

`refresh_html()` also opens that path in a browser the first time it succeeds
in a given run (tracked by `RunLog._browser_opened`, so a later tick's refresh
never reopens it), when the `RunLog` was constructed with `open_browser=True`.
`main.py` passes that from `ClientConfig.open_browser`, which defaults to on
and is wired to `--no-open-browser`; direct construction of a `RunLog` (every
test, and any future caller) defaults to off, so opening a browser is never a
side effect of code that isn't the real CLI entry point.

To regenerate one by hand (e.g. after editing an old log, or for a log someone
else sent you), `scripts/generate_run_summary.py` is a thin CLI wrapper around
the same `planet_charlu.run_summary.write_summary()` function main.py uses:

```bash
python scripts/generate_run_summary.py runs/p01.jsonl
# -> writes runs/p01-summary.html
```

`run_summary.py` has no dependency on any other `planet_charlu` module or
third-party library -- it only assumes the JSON Lines schema above, so it (or
a rewrite of it) can read a teammate's log even if they didn't use our client;
the CLI script adds `src/` to `sys.path` itself so it stays runnable with
nothing but a checkout, no `pip install` or `PYTHONPATH` needed. It renders a
single self-contained HTML file with two tabs.

**Overview**:

- Header stat tiles: final health, final inventory, decisions made,
  commands ok/failed, transactions settled.
- **Inventory over time** and **health over time** -- line charts with a
  hover crosshair + tooltip (mouse over the line to read exact values at a
  tick).
- **Decisions by kind** and **command outcomes** -- bar charts, so "did we
  spend our budget mostly seeking trades or gifting?" and "how often did we
  get rate-limited?" are visible at a glance.
- **Decision log** -- every decision in order, joined to its own `sent` terms
  and its `command_result` outcome, filterable by kind/partner/reason.
- **Resource flow** -- net given/received per resource across all settled
  trades.
- **Trade volume by partner** and a **filterable transaction table** (type a
  station ID to narrow the rows) -- for "did P02 actually reciprocate?"
  questions.

**Timeline** (`RunSummary.timeline_events()`): a resource-history chart
followed by one merged, chronological, filterable feed (toggle chips per type
plus a text filter) covering everything that happened in the run, without
reading the raw log line by line:

- **Completed trades** -- from `transaction_settled`.
- **Rejected requests** -- both our own commands the server turned down
  (`decision` joined to a `command_result` with `ok: false`) and incoming
  offers we declined (`offer_passed`).
- **Shortages** -- ticks with nonzero `last_unmet_upkeep`, grouped into
  contiguous spans (`shortage_periods()`) rather than one row per tick, so a
  ten-tick shortage reads as one event, not ten.
- **Disconnected periods** -- there is no explicit disconnect event in the
  schema (a real one just ends the run; `session.py` has no reconnect). This
  is inferred instead (`disconnected_periods()`): a gap between two
  consecutive events' `wall_time` much larger than the run's own median gap
  (at least 5x, floored at 5 seconds) is flagged as a likely stall, since a
  slow command/sync round trip can let several ticks pass with no
  `tick_snapshot` logged for them (`strategy.run_trading` only logs one when
  `world.tick` has actually changed since the last check) -- which, from the
  log alone, looks exactly like a dropped connection.

Colors and chart mechanics follow the project's data-viz conventions (fixed
categorical hue order for the three resources, status colors reserved for
ok/failed, a real hover layer rather than static images) -- see
`src/planet_charlu/run_summary.py`'s module docstring for the specifics.

This first draft is deliberately scoped to what the existing domain types
already expose cheaply. Ideas worth floating at the meeting for a v2, roughly
in order of how much new plumbing each needs:

1. **Advertisement lifecycle events** (`advertisement_posted` /
   `advertisement_expired`) -- right now advertisement activity is only
   visible indirectly, via `decision` events of kind `advertise`.
2. **A multi-run comparison mode** in the summary script -- point it at a
   directory of logs and get one table/chart per station, for comparing
   strategies across teams after a shared exercise.
3. **Cross-station timeline** -- if every team emits this schema, a
   post-exercise tool could merge everyone's `transaction_settled` events
   into one trade graph of the whole run.

## Try it

Unit tests, no server of any kind:

```bash
python -m pytest -q tests/test_structured_log.py tests/test_generate_run_summary.py \
  tests/test_logging_utils.py tests/test_domain_offers.py tests/test_domain_transactions.py \
  tests/test_scenario.py tests/test_config.py
```

**With the bundled local validator** (no live match needed -- see "Run the
practice exchange" in the README):

```bash
./scripts/run_validator.sh   # first shell, leave running
python -m planet_charlu.main --mode validation --ws-url ws://127.0.0.1:3001/ws
```

That alone now gets you everything: the live dashboard after each of the ten
steps, a `runs/P01-<timestamp>.jsonl` structured log, and
`runs/P01-<timestamp>-summary.html` auto-generated the moment it finishes.

**With no validator binary either**, `scripts/demo_local_run.py` spins up a
scripted in-process WebSocket server (the same technique
`tests/test_strategy.py::test_nine_planet_websocket_simulation` uses to test
`run_trading()` itself) and drives a real client through a few ticks of
trading against it, entirely offline:

```bash
PYTHONPATH=src python scripts/demo_local_run.py --run-log /tmp/demo.jsonl
python scripts/generate_run_summary.py /tmp/demo.jsonl
open /tmp/demo-summary.html   # or xdg-open on Linux
```

Against a real trading run (a live exercise) once one is available, no flags
needed beyond the usual endpoint/station/token:

```bash
python -m planet_charlu.main --mode trade \
  --ws-url wss://spaceport.edneo.com/ws --station-id P01
```
