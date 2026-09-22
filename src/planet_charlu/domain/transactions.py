"""Transaction domain type: a settled, completed exchange.

Distinct from ``Offer`` on purpose: an offer is a proposal that may never
settle, a transaction is proof two bundles already moved. Confusing the two
would risk double-counting a trade that a snapshot's inventory already
reflects.
"""

from __future__ import annotations

from dataclasses import dataclass

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
