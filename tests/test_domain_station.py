from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.station import StationSelf
from planet_charlu.generated import bazaar_pb2

from fixtures import make_bundle, make_self_observation


def test_from_wire_maps_core_fields():
    wire = make_self_observation(
        "P01",
        inventory=make_bundle(30, 30, 30),
        health=100,
        specialty=bazaar_pb2.RESOURCE_WATER,
    )

    self_view = StationSelf.from_wire(wire)

    assert self_view.station_id == "P01"
    assert self_view.inventory == Bundle(30, 30, 30)
    assert self_view.health == 100
    assert self_view.specialty is Resource.WATER


def test_had_full_upkeep_last_tick_true_when_unmet_is_zero():
    wire = make_self_observation(last_unmet_upkeep=make_bundle())

    assert StationSelf.from_wire(wire).had_full_upkeep_last_tick()


def test_had_full_upkeep_last_tick_false_when_something_was_missed():
    wire = make_self_observation(last_unmet_upkeep=make_bundle(0, 1, 0))

    assert not StationSelf.from_wire(wire).had_full_upkeep_last_tick()


def test_failed_once_and_shortage_counters_carry_through():
    wire = make_self_observation(
        failed_once=True, shortage_ticks=4, current_shortage_streak=2
    )

    self_view = StationSelf.from_wire(wire)

    assert self_view.failed_once is True
    assert self_view.shortage_ticks == 4
    assert self_view.current_shortage_streak == 2
