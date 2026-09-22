"""WorldView: one immutable, fully-formed snapshot of the world.

Built fresh from a whole ``bazaar_pb2.State`` via ``from_state`` -- never
mutated in place. A newer snapshot means calling ``from_state`` again and
replacing the caller's reference, not patching fields on an existing
instance. That structurally rules out the bug the brief warns about most:
re-applying a transaction a new snapshot's inventory already includes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from planet_charlu.domain.advertisements import Advertisement
from planet_charlu.domain.offers import Offer
from planet_charlu.domain.outcomes import CommandOutcome
from planet_charlu.domain.resources import Bundle
from planet_charlu.domain.station import StationSelf
from planet_charlu.domain.transactions import Transaction
from planet_charlu.generated import bazaar_pb2


@dataclass(frozen=True)
class WorldView:
    run_id: str
    tick: int
    phase: int
    world_version: int
    snapshot_sequence: int
    self_station_id: str
    self: StationSelf
    offers: Tuple[Offer, ...]
    advertisements: Tuple[Advertisement, ...]
    transactions: Tuple[Transaction, ...]
    request_results: Tuple[CommandOutcome, ...]

    @classmethod
    def from_state(cls, state: bazaar_pb2.State) -> "WorldView":
        return cls(
            run_id=state.run_id,
            tick=state.tick,
            phase=state.phase,
            world_version=state.world_version,
            snapshot_sequence=state.snapshot_sequence,
            self_station_id=state.self_station_id,
            self=StationSelf.from_wire(state.self),
            offers=tuple(Offer.from_wire(item) for item in state.offers.items),
            advertisements=tuple(
                Advertisement.from_wire(item) for item in state.advertisements.items
            ),
            transactions=tuple(
                Transaction.from_wire(item) for item in state.transactions.items
            ),
            request_results=tuple(
                CommandOutcome.from_wire(item) for item in state.request_results.items
            ),
        )

    def is_running(self) -> bool:
        return self.phase == bazaar_pb2.PHASE_RUNNING

    def open_offers_from_me(self) -> Tuple[Offer, ...]:
        return tuple(
            offer
            for offer in self.offers
            if offer.is_open() and offer.proposed_by(self.self_station_id)
        )

    def open_offers_to_me(self) -> Tuple[Offer, ...]:
        return tuple(
            offer
            for offer in self.offers
            if offer.is_open() and offer.directed_to(self.self_station_id)
        )

    def committed_bundle(self) -> Bundle:
        """Total resources promised away by our own currently open offers.

        Posting an offer "checks current ability to pay but locks nothing"
        server-side, so this is our own bookkeeping of what's already
        spoken for, not a server-verified reservation.
        """
        total = Bundle.zero()
        for offer in self.open_offers_from_me():
            total = total + offer.give
        return total

    def available_bundle(self) -> Bundle:
        """Inventory minus our own open commitments, clamped at zero.

        Clamped (not raised) because the server allows multiple offers to
        promise the same stock; if we've overcommitted on paper, the honest
        answer is "nothing left," not a crash.
        """
        return self.self.inventory.saturating_subtract(self.committed_bundle())
