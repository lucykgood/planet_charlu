"""Advertisement domain type: a claim about what a planet sells or seeks.

An advertisement proves nothing about actual stock or need ("the server
authenticates the claimant but does not verify the claimed stock or need")
-- it is a signal, not a fact about inventory. Keeping it a distinct type
from ``StationSelf``/``Bundle`` makes that distinction impossible to blur.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import FrozenSet

from planet_charlu.domain.resources import Resource, resource_from_wire
from planet_charlu.generated import bazaar_pb2


class PublicationStatus(enum.Enum):
    ACTIVE = "active"
    REPLACED = "replaced"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    RUN_ENDED = "run_ended"


_STATUS_FROM_WIRE = {
    bazaar_pb2.PUBLICATION_STATUS_ACTIVE: PublicationStatus.ACTIVE,
    bazaar_pb2.PUBLICATION_STATUS_REPLACED: PublicationStatus.REPLACED,
    bazaar_pb2.PUBLICATION_STATUS_WITHDRAWN: PublicationStatus.WITHDRAWN,
    bazaar_pb2.PUBLICATION_STATUS_EXPIRED: PublicationStatus.EXPIRED,
    bazaar_pb2.PUBLICATION_STATUS_RUN_ENDED: PublicationStatus.RUN_ENDED,
}


@dataclass(frozen=True)
class Advertisement:
    advertisement_id: str
    station_id: str
    selling: FrozenSet[Resource]
    seeking: FrozenSet[Resource]
    created_tick: int
    expires_tick: int
    status: PublicationStatus

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.Advertisement) -> "Advertisement":
        return cls(
            advertisement_id=wire.advertisement_id,
            station_id=wire.station_id,
            selling=frozenset(resource_from_wire(item) for item in wire.selling.items),
            seeking=frozenset(resource_from_wire(item) for item in wire.seeking.items),
            created_tick=wire.created_tick,
            expires_tick=wire.expires_tick,
            status=_STATUS_FROM_WIRE[wire.status],
        )

    def is_active(self) -> bool:
        return self.status is PublicationStatus.ACTIVE

    def is_expired_by(self, tick: int) -> bool:
        """Same exclusive-deadline rule as offers: expired at, not after, the tick."""
        return tick >= self.expires_tick

    def is_help_request(self) -> bool:
        """Empty selling plus nonempty seeking is a help request, per the brief."""
        return not self.selling and bool(self.seeking)

    def posted_by(self, station_id: str) -> bool:
        return self.station_id == station_id
