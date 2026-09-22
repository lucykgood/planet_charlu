"""StationSelf: this planet's own observed state.

Wraps ``bazaar_pb2.StationObservation``, keeping only the fields the
reserve-protection and survival-monitoring logic actually needs (per the
assignment brief's testing areas: protecting upkeep and detecting
shortages/failure). Fields not read anywhere yet are left off rather than
copied speculatively; add them when a real caller needs them.
"""

from __future__ import annotations

from dataclasses import dataclass

from planet_charlu.domain.resources import Bundle, Resource, resource_from_wire
from planet_charlu.generated import bazaar_pb2


@dataclass(frozen=True)
class StationSelf:
    station_id: str
    inventory: Bundle
    health: int
    specialty: Resource
    upkeep_per_tick: Bundle
    last_production: Bundle
    last_unmet_upkeep: Bundle
    shortage_ticks: int
    current_shortage_streak: int
    failed_once: bool

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.StationObservation) -> "StationSelf":
        return cls(
            station_id=wire.station_id,
            inventory=Bundle.from_wire(wire.inventory),
            health=wire.health,
            specialty=resource_from_wire(wire.specialty),
            upkeep_per_tick=Bundle.from_wire(wire.upkeep_per_tick),
            last_production=Bundle.from_wire(wire.last_production),
            last_unmet_upkeep=Bundle.from_wire(wire.last_unmet_upkeep),
            shortage_ticks=wire.shortage_ticks,
            current_shortage_streak=wire.current_shortage_streak,
            failed_once=wire.failed_once,
        )

    def had_full_upkeep_last_tick(self) -> bool:
        return self.last_unmet_upkeep.is_zero()
