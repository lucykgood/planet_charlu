"""Offer domain type: an exact proposed exchange, from the proposer's side.

``give``/``receive`` are always from the proposer's perspective, per the
wire schema. Wrapping the raw ``bazaar_pb2.Offer`` here means the rest of
the codebase asks ``offer.is_open()`` instead of comparing against a raw
``OFFER_STATUS_OPEN`` int scattered through decision code.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from planet_charlu.domain.resources import Bundle
from planet_charlu.generated import bazaar_pb2


class OfferStatus(enum.Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    RUN_ENDED = "run_ended"


_STATUS_FROM_WIRE = {
    bazaar_pb2.OFFER_STATUS_OPEN: OfferStatus.OPEN,
    bazaar_pb2.OFFER_STATUS_ACCEPTED: OfferStatus.ACCEPTED,
    bazaar_pb2.OFFER_STATUS_WITHDRAWN: OfferStatus.WITHDRAWN,
    bazaar_pb2.OFFER_STATUS_EXPIRED: OfferStatus.EXPIRED,
    bazaar_pb2.OFFER_STATUS_RUN_ENDED: OfferStatus.RUN_ENDED,
}


@dataclass(frozen=True)
class Offer:
    offer_id: str
    proposer_id: str
    recipient_id: str
    give: Bundle
    receive: Bundle
    created_tick: int
    expires_tick: int
    status: OfferStatus
    transaction_id: Optional[str]

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.Offer) -> "Offer":
        return cls(
            offer_id=wire.offer_id,
            proposer_id=wire.proposer_id,
            recipient_id=wire.recipient_id,
            give=Bundle.from_wire(wire.give),
            receive=Bundle.from_wire(wire.receive),
            created_tick=wire.created_tick,
            expires_tick=wire.expires_tick,
            status=_STATUS_FROM_WIRE[wire.status],
            transaction_id=None if wire.transaction_id.null else wire.transaction_id.value,
        )

    def is_open(self) -> bool:
        return self.status is OfferStatus.OPEN

    def is_expired_by(self, tick: int) -> bool:
        """True if this offer is unusable at ``tick``.

        ``expires_tick: 12`` means unusable *starting at* tick 12 (an
        exclusive deadline), so the boundary tick itself already counts as
        expired.
        """
        return tick >= self.expires_tick

    def is_gift(self) -> bool:
        """A gift is an ordinary offer with an all-zero receive bundle."""
        return self.receive.is_zero()

    def proposed_by(self, station_id: str) -> bool:
        return self.proposer_id == station_id

    def directed_to(self, station_id: str) -> bool:
        return self.recipient_id == station_id
