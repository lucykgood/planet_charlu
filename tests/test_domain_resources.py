import pytest

from planet_charlu.domain.resources import Bundle, Resource, resource_from_wire, resource_to_wire
from planet_charlu.generated import bazaar_pb2


def test_negative_quantity_raises():
    with pytest.raises(ValueError):
        Bundle(water=-1)


def test_zero_is_all_zero_fields():
    assert Bundle.zero() == Bundle(water=0, food=0, components=0)


def test_is_zero():
    assert Bundle.zero().is_zero()
    assert not Bundle(water=1).is_zero()


def test_wire_round_trip_preserves_zeros():
    wire = Bundle.zero().to_wire()
    assert wire.water == 0 and wire.food == 0 and wire.components == 0

    decoded = Bundle.from_wire(wire)
    assert decoded == Bundle.zero()


def test_from_wire_reads_real_values():
    wire = bazaar_pb2.Bundle()
    wire.water = 30
    wire.food = 5
    wire.components = 0

    assert Bundle.from_wire(wire) == Bundle(water=30, food=5, components=0)


def test_add_sums_each_field():
    assert Bundle(1, 2, 3) + Bundle(10, 20, 30) == Bundle(11, 22, 33)


def test_covers_true_when_affordable():
    assert Bundle(5, 5, 5).covers(Bundle(2, 0, 5))


def test_covers_false_when_any_field_short():
    assert not Bundle(5, 5, 5).covers(Bundle(6, 0, 0))


def test_minus_returns_strict_difference():
    assert Bundle(5, 5, 5).minus(Bundle(2, 0, 5)) == Bundle(3, 5, 0)


def test_minus_raises_when_not_affordable():
    with pytest.raises(ValueError):
        Bundle(1, 1, 1).minus(Bundle(2, 0, 0))


def test_saturating_subtract_clamps_at_zero_per_field():
    result = Bundle(1, 5, 0).saturating_subtract(Bundle(3, 2, 1))

    assert result == Bundle(0, 3, 0)


def test_resource_wire_round_trip_for_all_values():
    for resource in Resource:
        wire_value = resource_to_wire(resource)
        assert resource_from_wire(wire_value) is resource
