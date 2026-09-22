from planet_charlu.domain.advertisements import Advertisement, PublicationStatus
from planet_charlu.domain.resources import Resource
from planet_charlu.generated import bazaar_pb2

from fixtures import make_advertisement


def test_from_wire_maps_selling_and_seeking():
    wire = make_advertisement(
        selling=[bazaar_pb2.RESOURCE_WATER],
        seeking=[bazaar_pb2.RESOURCE_FOOD, bazaar_pb2.RESOURCE_COMPONENTS],
    )

    advertisement = Advertisement.from_wire(wire)

    assert advertisement.selling == frozenset({Resource.WATER})
    assert advertisement.seeking == frozenset({Resource.FOOD, Resource.COMPONENTS})
    assert advertisement.status is PublicationStatus.ACTIVE


def test_empty_selling_list_is_not_confused_with_omitted():
    wire = make_advertisement(selling=[], seeking=[bazaar_pb2.RESOURCE_COMPONENTS])

    advertisement = Advertisement.from_wire(wire)

    assert advertisement.selling == frozenset()
    assert advertisement.is_help_request()


def test_nonempty_selling_is_not_a_help_request():
    wire = make_advertisement(
        selling=[bazaar_pb2.RESOURCE_WATER], seeking=[bazaar_pb2.RESOURCE_FOOD]
    )

    assert not Advertisement.from_wire(wire).is_help_request()


def test_is_active_only_for_active_status():
    active = Advertisement.from_wire(make_advertisement(status=bazaar_pb2.PUBLICATION_STATUS_ACTIVE))
    replaced = Advertisement.from_wire(
        make_advertisement(status=bazaar_pb2.PUBLICATION_STATUS_REPLACED)
    )

    assert active.is_active()
    assert not replaced.is_active()


def test_expiry_is_exclusive_at_the_boundary_tick():
    advertisement = Advertisement.from_wire(make_advertisement(expires_tick=6))

    assert not advertisement.is_expired_by(5)
    assert advertisement.is_expired_by(6)


def test_posted_by():
    advertisement = Advertisement.from_wire(make_advertisement(station_id="P02"))

    assert advertisement.posted_by("P02")
    assert not advertisement.posted_by("P01")
