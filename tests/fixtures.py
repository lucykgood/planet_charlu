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
    station_id: str = "P01",
    *,
    inventory: bazaar_pb2.Bundle | None = None,
    health: int = 100,
    failed_once: bool = False,
    last_production: bazaar_pb2.Bundle | None = None,
    last_unmet_upkeep: bazaar_pb2.Bundle | None = None,
    upkeep_per_tick: bazaar_pb2.Bundle | None = None,
    shortage_ticks: int = 0,
    current_shortage_streak: int = 0,
    specialty: int = bazaar_pb2.RESOURCE_WATER,
) -> bazaar_pb2.StationObservation:
    self_obs = bazaar_pb2.StationObservation()
    self_obs.station_id = station_id
    self_obs.inventory.CopyFrom(inventory if inventory is not None else make_bundle(30, 30, 30))
    self_obs.health = health
    self_obs.failed_once = failed_once
    self_obs.first_failure_tick.null = True
    self_obs.last_production.CopyFrom(
        last_production if last_production is not None else make_bundle()
    )
    self_obs.last_unmet_upkeep.CopyFrom(
        last_unmet_upkeep if last_unmet_upkeep is not None else make_bundle()
    )
    self_obs.fully_supplied_ticks = 0
    self_obs.shortage_ticks = shortage_ticks
    self_obs.current_shortage_streak = current_shortage_streak
    self_obs.longest_shortage_streak = 0
    self_obs.produced_total.CopyFrom(make_bundle())
    self_obs.consumed_total.CopyFrom(make_bundle())
    self_obs.unmet_total.CopyFrom(make_bundle())
    self_obs.imported_total.CopyFrom(make_bundle())
    self_obs.exported_total.CopyFrom(make_bundle())
    self_obs.upkeep_per_tick.CopyFrom(
        upkeep_per_tick if upkeep_per_tick is not None else make_bundle(1, 1, 1)
    )
    self_obs.specialty = specialty
    return self_obs


def make_state(
    *,
    run_id: str = "test-run",
    snapshot_sequence: int = 1,
    world_version: int = 2,
    tick: int = 0,
    phase: int = bazaar_pb2.PHASE_RUNNING,
    station_id: str = "P01",
    self_observation: bazaar_pb2.StationObservation | None = None,
    offers: list[bazaar_pb2.Offer] | None = None,
    advertisements: list[bazaar_pb2.Advertisement] | None = None,
    transactions: list[bazaar_pb2.Transaction] | None = None,
    request_results: list[bazaar_pb2.Result] | None = None,
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
    state.self.CopyFrom(
        self_observation if self_observation is not None else make_self_observation(station_id)
    )
    state.offers.SetInParent()
    state.offers.items.extend(offers or [])
    state.advertisements.SetInParent()
    state.advertisements.items.extend(advertisements or [])
    state.transactions.SetInParent()
    state.transactions.items.extend(transactions or [])
    state.request_results.SetInParent()
    state.request_results.items.extend(request_results or [])
    state.outcome.null = True
    return state


def make_offer(
    *,
    offer_id: str = "offer-1",
    proposer_id: str = "P01",
    recipient_id: str = "P02",
    give: bazaar_pb2.Bundle | None = None,
    receive: bazaar_pb2.Bundle | None = None,
    created_tick: int = 0,
    expires_tick: int = 6,
    status: int = bazaar_pb2.OFFER_STATUS_OPEN,
    transaction_id: str | None = None,
) -> bazaar_pb2.Offer:
    offer = bazaar_pb2.Offer()
    offer.offer_id = offer_id
    offer.proposer_id = proposer_id
    offer.recipient_id = recipient_id
    offer.give.CopyFrom(give if give is not None else make_bundle(2))
    offer.receive.CopyFrom(receive if receive is not None else make_bundle(0, 1))
    offer.created_tick = created_tick
    offer.created_version = 3
    offer.expires_tick = expires_tick
    offer.status = status
    offer.closed_tick.null = True
    if transaction_id is None:
        offer.transaction_id.null = True
    else:
        offer.transaction_id.value = transaction_id
    return offer


def make_advertisement(
    *,
    advertisement_id: str = "advertisement-1",
    station_id: str = "P02",
    selling: list[int] | None = None,
    seeking: list[int] | None = None,
    created_tick: int = 0,
    expires_tick: int = 6,
    status: int = bazaar_pb2.PUBLICATION_STATUS_ACTIVE,
) -> bazaar_pb2.Advertisement:
    advertisement = bazaar_pb2.Advertisement()
    advertisement.advertisement_id = advertisement_id
    advertisement.station_id = station_id
    advertisement.selling.items.extend(
        selling if selling is not None else [bazaar_pb2.RESOURCE_FOOD]
    )
    advertisement.seeking.items.extend(
        seeking if seeking is not None else [bazaar_pb2.RESOURCE_WATER]
    )
    advertisement.created_tick = created_tick
    advertisement.expires_tick = expires_tick
    advertisement.created_version = 2
    advertisement.status = status
    return advertisement


def make_transaction(
    *,
    transaction_id: str = "transaction-1",
    offer_id: str = "offer-1",
    proposer_id: str = "P01",
    recipient_id: str = "P02",
    give: bazaar_pb2.Bundle | None = None,
    receive: bazaar_pb2.Bundle | None = None,
    settled_tick: int = 0,
) -> bazaar_pb2.Transaction:
    transaction = bazaar_pb2.Transaction()
    transaction.transaction_id = transaction_id
    transaction.offer_id = offer_id
    transaction.proposer_id = proposer_id
    transaction.recipient_id = recipient_id
    transaction.give.CopyFrom(give if give is not None else make_bundle(2))
    transaction.receive.CopyFrom(receive if receive is not None else make_bundle(0, 1))
    transaction.settled_tick = settled_tick
    transaction.settled_version = 4
    return transaction


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
    object_id: str | None = None,
    transaction_id: str | None = None,
    retry_after_tick: int | None = None,
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
    if object_id is None:
        result.object_id.null = True
    else:
        result.object_id.value = object_id
    if transaction_id is None:
        result.transaction_id.null = True
    else:
        result.transaction_id.value = transaction_id
    if retry_after_tick is None:
        result.retry_after_tick.null = True
    else:
        result.retry_after_tick.value = retry_after_tick
    return result


def make_protocol_error(
    *,
    code: int = bazaar_pb2.CONTROL_CODE_BAD_MESSAGE,
    close_session: bool = False,
    request_id: str | None = None,
) -> bazaar_pb2.ProtocolError:
    error = bazaar_pb2.ProtocolError()
    error.type = bazaar_pb2.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR
    error.protocol_version = "2.0"
    error.run_id.null = True
    if request_id is None:
        error.request_id.null = True
    else:
        error.request_id.value = request_id
    error.code = code
    error.close_session = close_session
    return error
