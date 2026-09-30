"""Settled exchanges already reflected in snapshot inventory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from planet_charlu.domain.resources import Bundle
from planet_charlu.generated import bazaar_pb2


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    offer_id: str
    proposer_id: str
    recipient_id: str
    give: Bundle
    receive: Bundle
    settled_tick: int

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.Transaction) -> "Transaction":
        return cls(
            transaction_id=wire.transaction_id,
            offer_id=wire.offer_id,
            proposer_id=wire.proposer_id,
            recipient_id=wire.recipient_id,
            give=Bundle.from_wire(wire.give),
            receive=Bundle.from_wire(wire.receive),
            settled_tick=wire.settled_tick,
        )

    def involves(self, station_id: str) -> bool:
        return self.proposer_id == station_id or self.recipient_id == station_id

    def relative_to(self, station_id: str) -> Tuple[str, Bundle, Bundle]:
        """Resolve wire's proposer/recipient framing to ``station_id``'s own view.

        Returns ``(counterparty_id, we_gave, we_received)``. The wire
        ``give``/``receive`` are from the *proposer's* perspective, so a
        recipient's actual gave/received is the reverse of a proposer's.
        """
        if self.proposer_id == station_id:
            return self.recipient_id, self.give, self.receive
        return self.proposer_id, self.receive, self.give
