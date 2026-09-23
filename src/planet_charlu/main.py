from __future__ import annotations

import asyncio
import logging

from planet_charlu.config import ClientConfig, ConfigError, load_config
from planet_charlu.connection import BazaarConnection
from planet_charlu.logging_utils import configure_logging
from planet_charlu.scenario import ScenarioError, run_sample_scenario
from planet_charlu.session import open_session
from planet_charlu.strategy import run_trading

logger = logging.getLogger(__name__)


async def _run_client(config: ClientConfig) -> None:
    async with BazaarConnection(config) as connection:
        client_session = await open_session(connection)
        try:
            if config.mode == "validation":
                world = await run_sample_scenario(client_session)
            else:
                world = await run_trading(client_session)
        finally:
            await client_session.stop_pump()
    logger.info(
        "client completed: final inventory=%s, %d transaction(s)",
        world.self.inventory,
        len(world.transactions),
    )


def main() -> None:
    configure_logging()

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        raise SystemExit(1) from exc

    logger.info("starting Planet CharLu client: %r", config)

    try:
        asyncio.run(_run_client(config))
    except KeyboardInterrupt:
        logger.info("interrupted by user")
    except (RuntimeError, ConnectionError) as exc:
        logger.error("client stopped: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
