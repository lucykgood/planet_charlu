"""Drives the validator's guided ten-step trading exercise end-to-end.

Steps and request IDs follow validator/README.md's "Complete the exchange"
section exactly. Step 1 (initial state + readiness) is already satisfied by
``session.open_session()``; this module drives steps 2-10 on top of an
already-open ``ClientSession``.
"""

from __future__ import annotations

import logging

from planet_charlu import codec
from planet_charlu.commands import ProtocolErrorReceived
from planet_charlu.domain.offers import OfferStatus
from planet_charlu.domain.resources import Bundle, Resource
from planet_charlu.domain.world import WorldView
from planet_charlu.generated import bazaar_pb2
from planet_charlu.session import ClientSession

logger = logging.getLogger(__name__)


class ScenarioError(RuntimeError):
    """Raised when the server's responses stop matching the documented exercise.

    There is nothing sensible to do at that point except stop: a step out of
    order ends the server's own scripted exercise too (`scenario mismatch`),
    so continuing to send later steps would not recover anything.
    """


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioError(message)


async def run_sample_scenario(session: ClientSession) -> WorldView:
    """Run steps 2-10 of the guided exercise and return the final ``WorldView``."""
    run_id = session.world.run_id

    # Step 2: advertise water for food.
    outcome = await session.send(
        request_id="student-advertise-1",
        message=codec.build_advertise(
            run_id=run_id,
            request_id="student-advertise-1",
            selling=[Resource.WATER],
            seeking=[Resource.FOOD],
            expires_tick=6,
        ),
    )
    _require(outcome.ok, "step 2: advertise water for food was rejected")
    await session.wait_for(lambda world: world.world_version >= 3)
    logger.info("scenario step 2/10 done: advertised water for food")

    # Step 3: replace the advertisement with a request for components.
    outcome = await session.send(
        request_id="student-advertise-seeking-1",
        message=codec.build_advertise(
            run_id=run_id,
            request_id="student-advertise-seeking-1",
            selling=[],
            seeking=[Resource.COMPONENTS],
            expires_tick=6,
        ),
    )
    _require(outcome.ok, "step 3: advertise seeking components was rejected")
    advertisement_id = outcome.object_id
    _require(advertisement_id is not None, "step 3: result had no object_id")
    await session.wait_for(lambda world: world.world_version >= 4)
    logger.info("scenario step 3/10 done: advertisement_id=%s", advertisement_id)

    # Step 4: offer two water for one food.
    outcome = await session.send(
        request_id="student-offer-1",
        message=codec.build_offer(
            run_id=run_id,
            request_id="student-offer-1",
            recipient_id="P02",
            give=Bundle(water=2),
            receive=Bundle(food=1),
            expires_tick=6,
        ),
    )
    _require(outcome.ok, "step 4: offer was rejected")
    our_offer_id = outcome.object_id
    _require(our_offer_id is not None, "step 4: result had no object_id")
    logger.info("scenario step 4/10 done: offer_id=%s", our_offer_id)

    # Steps 5-6: P02 accepts our offer and separately offers us a gift, both
    # arriving as state pushes with no command of ours in between.
    world = await session.wait_for(
        lambda world: any(
            offer.offer_id == our_offer_id and offer.status is OfferStatus.ACCEPTED
            for offer in world.offers
        )
    )
    gift = next((offer for offer in world.open_offers_to_me() if offer.is_gift()), None)
    _require(gift is not None, "steps 5-6: no gift offer from P02 after our offer was accepted")
    logger.info("scenario steps 5-6/10 done: gift offer_id=%s", gift.offer_id)

    # Step 7: accept the gift.
    outcome = await session.send(
        request_id="student-accept-1",
        message=codec.build_accept(
            run_id=run_id, request_id="student-accept-1", offer_id=gift.offer_id
        ),
    )
    _require(outcome.ok, "step 7: accepting the gift was rejected")
    await session.wait_for(lambda world: world.world_version >= 8)
    logger.info("scenario step 7/10 done: accepted gift")

    # Step 8: withdraw the advertisement.
    outcome = await session.send(
        request_id="student-withdraw-1",
        message=codec.build_withdraw(
            run_id=run_id, request_id="student-withdraw-1", object_id=advertisement_id
        ),
    )
    _require(outcome.ok, "step 8: withdrawing the advertisement was rejected")
    await session.wait_for(lambda world: world.world_version >= 9)
    logger.info("scenario step 8/10 done: withdrew advertisement")

    # Step 9: the exercise's stored-result limit (five) is deliberately
    # exceeded by this command; the server answers with protocol_error
    # instead of a result.
    try:
        await session.send(
            request_id="student-advertise-2",
            message=codec.build_advertise(
                run_id=run_id,
                request_id="student-advertise-2",
                selling=[Resource.WATER],
                seeking=[Resource.FOOD],
                expires_tick=6,
            ),
        )
    except ProtocolErrorReceived as error:
        _require(
            error.code == bazaar_pb2.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
            f"step 9: expected CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED, "
            f"got {bazaar_pb2.ControlCode.Name(error.code)}",
        )
    else:
        raise ScenarioError("step 9: expected a protocol_error, command succeeded instead")
    logger.info("scenario step 9/10 done: confirmed request-capacity error")

    # Step 10: sync and return the final state.
    world = await session.sync()
    logger.info(
        "scenario step 10/10 done: final inventory=%s, %d transaction(s)",
        world.self.inventory,
        len(world.transactions),
    )
    return world
