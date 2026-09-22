"""Factories for minimal, fully-initialized Bazaar message fixtures.

``bazaar.proto`` uses proto2 ``required`` fields, so a message must have
every required field explicitly set (including required *message* fields
that hold no other required data, such as list wrappers) before it can be
serialized. These helpers build the smallest state/readiness messages that
satisfy that contract, matching the practice server's step-1 scenario.
"""

from __future__ import annotations

from planet_charlu.generated import bazaar_pb2


def make_bundle(water: int = 0, food: int = 0, components: int = 0) -> bazaar_pb2.Bundle:
    bundle = bazaar_pb2.Bundle()
    bundle.water = water
    bundle.food = food
    bundle.components = components
    return bundle


def make_rules() -> bazaar_pb2.PublicRules:
    rules = bazaar_pb2.PublicRules()
    rules.rules_version = "test-rules-1"
    rules.duration_ticks = 100
    rules.tick_duration_ms = 1000
    rules.resource_order.items.extend(
        [bazaar_pb2.RESOURCE_WATER, bazaar_pb2.RESOURCE_FOOD, bazaar_pb2.RESOURCE_COMPONENTS]
    )
    rules.max_health = 100
    rules.shortage_damage_per_unit = 5
    rules.recovery_per_fully_supplied_tick = 5
    rules.max_publication_ttl_ticks = 50
    rules.max_offer_ttl_ticks = 50
    rules.new_commands_per_station_per_tick = 5
    rules.max_request_records_per_station = 5
    rules.max_open_outgoing_offers = 10
    rules.max_command_bytes = 16384
    return rules


def make_self_observation(
    station_id: str = "P01", *, inventory: bazaar_pb2.Bundle | None = None, health: int = 100
) -> bazaar_pb2.StationObservation:
    self_obs = bazaar_pb2.StationObservation()
    self_obs.station_id = station_id
    self_obs.inventory.CopyFrom(inventory if inventory is not None else make_bundle(30, 30, 30))
    self_obs.health = health
    self_obs.failed_once = False
    self_obs.first_failure_tick.null = True
    self_obs.last_production.CopyFrom(make_bundle())
    self_obs.last_unmet_upkeep.CopyFrom(make_bundle())
    self_obs.fully_supplied_ticks = 0
    self_obs.shortage_ticks = 0
    self_obs.current_shortage_streak = 0
    self_obs.longest_shortage_streak = 0
    self_obs.produced_total.CopyFrom(make_bundle())
    self_obs.consumed_total.CopyFrom(make_bundle())
    self_obs.unmet_total.CopyFrom(make_bundle())
    self_obs.imported_total.CopyFrom(make_bundle())
    self_obs.exported_total.CopyFrom(make_bundle())
    self_obs.upkeep_per_tick.CopyFrom(make_bundle(1, 1, 1))
    self_obs.specialty = bazaar_pb2.RESOURCE_WATER
    return self_obs


def make_state(
    *,
    run_id: str = "test-run",
    snapshot_sequence: int = 1,
    world_version: int = 2,
    tick: int = 0,
    phase: int = bazaar_pb2.PHASE_RUNNING,
    station_id: str = "P01",
) -> bazaar_pb2.State:
    state = bazaar_pb2.State()
    state.type = bazaar_pb2.STATE_TYPE_STATE
    state.protocol_version = "2.0"
    state.run_id = run_id
    state.snapshot_sequence = snapshot_sequence
    state.world_version = world_version
    state.tick = tick
    state.phase = phase
    state.self_station_id = station_id
    state.rules.CopyFrom(make_rules())
    state.directory.SetInParent()
    state.self.CopyFrom(make_self_observation(station_id))
    state.offers.SetInParent()
    state.advertisements.SetInParent()
    state.transactions.SetInParent()
    state.request_results.SetInParent()
    state.outcome.null = True
    return state


def make_readiness(
    *, run_id: str = "test-run", ready: bool = True, snapshot_sequence: int = 1
) -> bazaar_pb2.Readiness:
    readiness = bazaar_pb2.Readiness()
    readiness.type = bazaar_pb2.READINESS_TYPE_READINESS
    readiness.protocol_version = "2.0"
    readiness.run_id = run_id
    readiness.ready = ready
    readiness.snapshot_sequence = snapshot_sequence
    return readiness


def make_result(
    *,
    run_id: str = "test-run",
    request_id: str = "req-1",
    ok: bool = True,
    code: int = bazaar_pb2.RESULT_CODE_OK,
) -> bazaar_pb2.Result:
    result = bazaar_pb2.Result()
    result.type = bazaar_pb2.RESULT_TYPE_RESULT
    result.protocol_version = "2.0"
    result.run_id = run_id
    result.request_id = request_id
    result.ok = ok
    result.code = code
    result.processed_tick = 0
    result.processed_version = 3
    result.object_id.null = True
    result.transaction_id.null = True
    result.retry_after_tick.null = True
    return result


def make_protocol_error(
    *, code: int = bazaar_pb2.CONTROL_CODE_BAD_MESSAGE, close_session: bool = False
) -> bazaar_pb2.ProtocolError:
    error = bazaar_pb2.ProtocolError()
    error.type = bazaar_pb2.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR
    error.protocol_version = "2.0"
    error.run_id.null = True
    error.request_id.null = True
    error.code = code
    error.close_session = close_session
    return error
