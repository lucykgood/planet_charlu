"""Nonnegative resource bundles and wire enum conversion.

Use minus() for affordable payments and saturating_subtract() for estimates
that may be overcommitted, such as inventory less outstanding offers.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from planet_charlu.generated import bazaar_pb2


class Resource(enum.Enum):
    WATER = "water"
    FOOD = "food"
    COMPONENTS = "components"


_RESOURCE_FROM_WIRE = {
    bazaar_pb2.RESOURCE_WATER: Resource.WATER,
    bazaar_pb2.RESOURCE_FOOD: Resource.FOOD,
    bazaar_pb2.RESOURCE_COMPONENTS: Resource.COMPONENTS,
}
_RESOURCE_TO_WIRE = {value: key for key, value in _RESOURCE_FROM_WIRE.items()}


def resource_from_wire(value: int) -> Resource:
    return _RESOURCE_FROM_WIRE[value]


def resource_to_wire(resource: Resource) -> int:
    return _RESOURCE_TO_WIRE[resource]


@dataclass(frozen=True)
class Bundle:
    water: int = 0
    food: int = 0
    components: int = 0

    def __post_init__(self) -> None:
        if self.water < 0 or self.food < 0 or self.components < 0:
            raise ValueError(f"bundle quantities must be non-negative, got {self}")

    @classmethod
    def zero(cls) -> "Bundle":
        return cls()

    @classmethod
    def from_wire(cls, wire: bazaar_pb2.Bundle) -> "Bundle":
        return cls(water=wire.water, food=wire.food, components=wire.components)

    def to_wire(self) -> bazaar_pb2.Bundle:
        wire = bazaar_pb2.Bundle()
        wire.water = self.water
        wire.food = self.food
        wire.components = self.components
        return wire

    def is_zero(self) -> bool:
        return self.water == 0 and self.food == 0 and self.components == 0

    def __add__(self, other: "Bundle") -> "Bundle":
        return Bundle(
            water=self.water + other.water,
            food=self.food + other.food,
            components=self.components + other.components,
        )

    def covers(self, cost: "Bundle") -> bool:
        """True if paying ``cost`` out of this bundle would not go negative."""
        return (
            self.water >= cost.water
            and self.food >= cost.food
            and self.components >= cost.components
        )

    def minus(self, cost: "Bundle") -> "Bundle":
        """Subtract an affordable payment; raise if any resource is short."""
        if not self.covers(cost):
            raise ValueError(f"{cost} exceeds {self}")
        return Bundle(
            water=self.water - cost.water,
            food=self.food - cost.food,
            components=self.components - cost.components,
        )

    def saturating_subtract(self, cost: "Bundle") -> "Bundle":
        """Subtract an estimated cost, clamping shortfalls to zero."""
        return Bundle(
            water=max(0, self.water - cost.water),
            food=max(0, self.food - cost.food),
            components=max(0, self.components - cost.components),
        )
