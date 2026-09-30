"""Structured, machine-readable run log for post-run summaries.

Writes one JSON object per line (newline-delimited JSON) to a file, one line
per notable event in a trading run: the run starting, each tick's snapshot,
the offers actually open each tick, each decision the strategy makes (with
the exact terms sent, not just why), each incoming offer it considered and
declined, each command result, each settled transaction, and the run ending.
Together these let a reader reconstruct, for any offer, what the client knew,
what it decided and why, what it actually sent, and what the server
confirmed. ``run_summary.py`` reads this file back and renders a human-facing
summary from it.

This is deliberately independent of ``logging_utils.py``: that module makes
the existing text log human-readable in a terminal; this one makes the run
machine-readable for after-the-fact analysis, and is safe to also feed to a
teammate's or grader's tooling. Every write is flushed immediately so a
killed process still leaves a usable partial log -- there is no reconnect or
retry in this client, so the log is often the only record of how a run
actually ended.

Field names intentionally mirror the domain dataclasses
(``domain/resources.py``, ``domain/station.py``, ``domain/transactions.py``,
``domain/outcomes.py``) so a reader familiar with the client code recognizes
them immediately.
"""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional

from planet_charlu.domain.offers import Offer
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.resources import Bundle, resource_from_wire
from planet_charlu.domain.transactions import Transaction
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2 as pb

SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)


def _bundle_dict(bundle: Bundle) -> dict:
    return {"water": bundle.water, "food": bundle.food, "components": bundle.components}


def _message_fields(message: pb.ClientMessage) -> dict:
    """The exact terms of an outgoing command -- what actually got sent, not
    just the free-text reason a caller also logs alongside it.
    """
    kind = message.WhichOneof("message")
    if kind == "offer":
        body = message.offer.body
        return {
            "command": "offer",
            "recipient_id": body.recipient_id,
            "give": _bundle_dict(Bundle.from_wire(body.give)),
            "receive": _bundle_dict(Bundle.from_wire(body.receive)),
            "expires_tick": body.expires_tick,
        }
    if kind == "accept":
        return {"command": "accept", "offer_id": message.accept.body.offer_id}
    if kind == "withdraw":
        return {"command": "withdraw", "object_id": message.withdraw.body.object_id}
    if kind == "advertise":
        body = message.advertise.body
        return {
            "command": "advertise",
            "selling": [resource_from_wire(r).value for r in body.selling.items],
            "seeking": [resource_from_wire(r).value for r in body.seeking.items],
            "expires_tick": body.expires_tick,
        }
    return {"command": kind}


def _open_in_browser(path: Path) -> None:
    """Best-effort: a headless environment (a Docker container with no
    display, CI) has no browser to open, and that must never take down the
    run it's reporting on.
    """
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:
        logger.exception("failed to open %s in a browser", path)


@dataclass(frozen=True)
class RunLog:
    """Append-only JSON Lines writer for one run's structured log.

    Use as a context manager so the file is always closed:

        with RunLog.open(path, station_id=..., mode="trade") as run_log:
            ...
            run_log.run_started(world)
            ...

    Passing ``None`` in place of a ``RunLog`` (the default everywhere it is
    accepted) disables logging entirely; callers do not need an ``if
    run_log:`` guard around every call site because ``run_started`` etc. are
    simply not called on ``None``. Call sites that always have a run in hand
    use :class:`RunLog` directly; ``main.py`` is the only place a run log is
    optional.

    ``open_browser`` defaults to ``False`` so constructing a ``RunLog``
    directly (every test, and any future caller) never has a side effect on
    the machine it runs on; only ``main.py`` -- the real CLI entry point --
    opts in, driven by ``ClientConfig.open_browser``.
    """

    path: Path
    _file: IO[str]
    _seen_transactions: set
    open_browser: bool = False
    _browser_opened: list = field(default_factory=list)

    @classmethod
    def open(cls, path: str | Path, *, open_browser: bool = False) -> "RunLog":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", encoding="utf-8")
        return cls(path=path, _file=handle, _seen_transactions=set(), open_browser=open_browser)

    def __enter__(self) -> "RunLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._file.close()

    def refresh_html(self) -> Optional[Path]:
        """Regenerate this run's HTML summary right now, so a page already
        open in a browser can pick up progress on its next reload instead of
        only ever showing the state as of when the run finished. Returns the
        path written, or ``None`` if rendering failed.

        Imported locally (not at module level) so a summary-rendering bug can
        never become an import-time failure for every caller of this module;
        and best-effort for the same reason a rendering hiccup must not be
        allowed to interrupt the run it's reporting on.

        The very first successful write of a run also opens the page in a
        browser, when ``open_browser`` was requested -- once only per
        ``RunLog``, tracked by ``_browser_opened``, so a live-updating page
        already open doesn't get a fresh tab shoved at the user on every tick.
        """
        try:
            from planet_charlu.run_summary import write_summary
            out_path = write_summary(self.path)
        except Exception:
            logger.exception("failed to refresh HTML summary from %s", self.path)
            return None
        if self.open_browser and not self._browser_opened:
            self._browser_opened.append(True)
            _open_in_browser(out_path)
        return out_path

    def _write(self, event: str, tick: Optional[int], fields: dict) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "wall_time": time.time(),
            "tick": tick,
            **fields,
        }
        self._file.write(json.dumps(record, sort_keys=True))
        self._file.write("\n")
        self._file.flush()

    def run_started(self, world: WorldView, *, mode: str) -> None:
        self._write(
            "run_started",
            world.tick,
            {
                "run_id": world.run_id,
                "mode": mode,
                "self_station_id": world.self_station_id,
                "specialty": world.self.specialty.value,
                "duration_ticks": world.rules.duration_ticks,
                "max_request_records_per_station": world.rules.max_request_records_per_station,
                "starting_inventory": _bundle_dict(world.self.inventory),
                "upkeep_per_tick": _bundle_dict(world.self.upkeep_per_tick),
            },
        )

    def tick_snapshot(self, world: WorldView, *, sent_total: int) -> None:
        self._write(
            "tick_snapshot",
            world.tick,
            {
                "phase": world.phase,
                "health": world.self.health,
                "inventory": _bundle_dict(world.self.inventory),
                "last_production": _bundle_dict(world.self.last_production),
                "last_unmet_upkeep": _bundle_dict(world.self.last_unmet_upkeep),
                "current_shortage_streak": world.self.current_shortage_streak,
                "open_offers_from_me": len(world.open_offers_from_me()),
                "open_offers_to_me": len(world.open_offers_to_me()),
                "active_advertisements": sum(
                    1 for a in world.advertisements if a.is_active() and not a.is_expired_by(world.tick)
                ),
                "budget_used": sent_total,
                "budget_max": world.rules.max_request_records_per_station,
            },
        )

    def decision(self, world: WorldView, *, key: str, reason: str, request_id: str,
                 message: Optional[pb.ClientMessage] = None) -> None:
        """Log one attempted action, identified by its own dedup ``key``.

        Takes plain fields rather than ``strategy.Decision`` so any caller can
        log a decision -- ``scenario.py``'s fixed ten-step exercise reports
        its own steps through the same method, not just the continuous
        trading loop. ``message`` is the actual wire command, when the caller
        has it -- it fills in ``sent`` with the exact terms (amounts,
        recipient, expiry), since ``reason`` alone is a human sentence, not a
        record of what was actually offered.
        """
        prefix = key.split(":", 1)[0]
        # 'partner:<station>' covers two different actions in strategy.py
        # (seek a needed resource vs. gift surplus specialty); split them by
        # their distinct reason text so a summary can tell them apart
        # without re-deriving the strategy's own decision logic.
        if prefix == "partner":
            kind = "gift" if reason.startswith("share surplus") else "seek_trade"
        else:
            kind = prefix
        fields = {
            "key": key,
            "kind": kind,
            "reason": reason,
            "request_id": request_id,
        }
        if message is not None:
            fields["sent"] = _message_fields(message)
        self._write("decision", world.tick, fields)

    def command_result(self, world: WorldView, *, key: str, outcome: CommandOutcome) -> None:
        self._write(
            "command_result",
            world.tick,
            {
                "key": key,
                "request_id": outcome.request_id,
                "ok": outcome.ok,
                "code": outcome.code_name,
                "object_id": outcome.object_id,
                "transaction_id": outcome.transaction_id,
                "retry_after_tick": outcome.retry_after_tick,
            },
        )

    def offers_snapshot(self, world: WorldView) -> None:
        """What our client actually knew about the offer board this tick.

        ``tick_snapshot`` only counts open offers; this logs their real
        terms, so "what did it know when it decided" doesn't have to be
        inferred from counts. Skipped when there is nothing open, to avoid a
        content-free line on every quiet tick.
        """
        self_id = world.self_station_id
        outgoing = [o for o in world.open_offers_from_me() if not o.is_expired_by(world.tick)]
        incoming = [o for o in world.open_offers_to_me() if not o.is_expired_by(world.tick)]
        if not outgoing and not incoming:
            return

        def describe(offer: Offer) -> dict:
            other, we_give, we_receive = offer.relative_to(self_id)
            return {
                "offer_id": offer.offer_id,
                "counterparty": other,
                "we_give": _bundle_dict(we_give),
                "we_receive": _bundle_dict(we_receive),
                "expires_tick": offer.expires_tick,
            }

        self._write(
            "offers_open",
            world.tick,
            {
                "outgoing": [describe(o) for o in outgoing],
                "incoming": [describe(o) for o in incoming],
            },
        )

    def offer_passed(self, world: WorldView, offer: Offer, *, reason: str) -> None:
        """An incoming offer the strategy looked at and did not accept.

        Without this, only the one offer eventually accepted (if any) leaves
        a trace; every offer considered and declined -- unfavorable terms,
        insufficient stock, an exhausted budget -- would otherwise vanish
        the moment a newer snapshot replaces it.
        """
        other, we_would_give, we_would_receive = offer.relative_to(world.self_station_id)
        self._write(
            "offer_passed",
            world.tick,
            {
                "offer_id": offer.offer_id,
                "counterparty": other,
                "we_would_give": _bundle_dict(we_would_give),
                "we_would_receive": _bundle_dict(we_would_receive),
                "reason": reason,
            },
        )

    def transactions_settled(self, world: WorldView) -> None:
        """Log any transaction in ``world`` involving us that hasn't been logged yet.

        Snapshots are complete, not incremental, so the same settled transaction
        reappears in every later snapshot; this call is idempotent per
        transaction id, mirroring ``SelfSufficientStrategy._learn_suppliers``.
        """
        for tx in world.transactions:
            if tx.transaction_id in self._seen_transactions or not tx.involves(world.self_station_id):
                continue
            self._seen_transactions.add(tx.transaction_id)
            self._log_transaction(world.self_station_id, tx)

    def _log_transaction(self, self_station_id: str, tx: Transaction) -> None:
        counterparty, we_gave, we_received = tx.relative_to(self_station_id)
        self._write(
            "transaction_settled",
            tx.settled_tick,
            {
                "transaction_id": tx.transaction_id,
                "offer_id": tx.offer_id,
                "counterparty": counterparty,
                "we_gave": _bundle_dict(we_gave),
                "we_received": _bundle_dict(we_received),
            },
        )

    def run_ended(self, world: WorldView, *, reason: str) -> None:
        self._write(
            "run_ended",
            world.tick,
            {
                "reason": reason,
                "phase": world.phase,
                "health": world.self.health,
                "failed_once": world.self.failed_once,
                "final_inventory": _bundle_dict(world.self.inventory),
                "transaction_count": sum(1 for tx in world.transactions if tx.involves(world.self_station_id)),
            },
        )
        # Always refresh here, not just left to the caller: this is the one
        # write after which the HTML must stop showing "Live" and auto-
        # reloading, and a caller (a script, a test) forgetting to also call
        # refresh_html() separately must not leave the page stuck live forever.
        self.refresh_html()
