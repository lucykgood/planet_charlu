"""Live smoke test of the real scenario driver (planet_charlu.scenario) against
a running validator, checking the exact documented values from
validator/README.md that scenario.py itself only checks loosely (e.g. it
waits for world_version >= 8, this asserts the final inventory is exactly
(28,31,31)). NOT part of the deliverable -- untracked scratch code.

Each validator run_id can complete this scripted exercise only once, in
order, so run this against a FRESH validator (restart it between runs):

    docker compose exec app bash -lc "python manual_playground.py"
"""

from __future__ import annotations

import asyncio
import logging

from planet_charlu.config import load_config
from planet_charlu.connection import BazaarConnection
from planet_charlu.domain.resources import Bundle
from planet_charlu.logging_utils import configure_logging
from planet_charlu.scenario import run_sample_scenario
from planet_charlu.session import open_session

logger = logging.getLogger("manual_test")


class CheckFailed(AssertionError):
    pass


def check(condition: bool, detail: str) -> None:
    if not condition:
        raise CheckFailed(f"FAILED check: {detail}")
    logger.info("  check ok: %s", detail)


async def main() -> None:
    configure_logging()
    config = load_config([])
    logger.info("using config: %s", config)

    async with BazaarConnection(config) as connection:
        session = await open_session(connection)
        check(session.world.world_version == 2, "step 1: world_version == 2")
        check(session.world.snapshot_sequence == 1, "step 1: snapshot_sequence == 1")
        check(session.world.self.inventory == Bundle(30, 30, 30), "step 1: inventory (30,30,30)")

        world = await run_sample_scenario(session)

        check(world.world_version == 9, "step 10: world_version == 9")
        check(world.snapshot_sequence == 9, "step 10: snapshot_sequence == 9")
        check(world.self.inventory == Bundle(28, 31, 31), "step 10: final inventory (28,31,31)")
        check(len(world.transactions) == 2, "step 10: two transactions total")
        check(len(world.request_results) == 5, "step 10: five stored request_results")

        logger.info("ALL 10 STEPS PASSED (via scenario.run_sample_scenario)")
        await session.stop_pump()


if __name__ == "__main__":
    asyncio.run(main())
