from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext

from planet_charlu.config import ClientConfig, ConfigError, load_config
from planet_charlu.connection import BazaarConnection
from planet_charlu.logging_utils import configure_logging
from planet_charlu.run_summary import write_summary
from planet_charlu.scenario import run_sample_scenario
from planet_charlu.session import open_session
from planet_charlu.strategy import run_trading
from planet_charlu.structured_log import RunLog

logger = logging.getLogger(__name__)


async def _run_client(config: ClientConfig) -> None:
    world = None
    run_log = None
    run_log_context = (
        RunLog.open(config.run_log_path, open_browser=config.open_browser)
        if config.run_log_path else nullcontext()
    )
    try:
        with run_log_context as run_log:
            if run_log:
                logger.info("structured run log: %s", config.run_log_path)
            async with BazaarConnection(config) as connection:
                client_session = await open_session(connection)
                try:
                    if config.mode == "validation":
                        world = await run_sample_scenario(client_session, run_log=run_log)
                    else:
                        world = await run_trading(client_session, run_log=run_log,
                                                   conservative=config.conservative_trading)
                finally:
                    await client_session.stop_pump()
    finally:
        # Written even on failure (a timeout, a rejected step, a dropped
        # connection): a partial dashboard of how far the run got is still
        # useful, and there is no reconnect to try again with. Goes through
        # run_log.refresh_html() when available so a run that fails before
        # its first tick (never reaching strategy.py's own refresh_html
        # calls) still gets this as its "first write" and, if requested,
        # still opens a browser to it -- not just a bare write_summary call.
        if config.run_log_path:
            try:
                if run_log is not None:
                    summary_path = run_log.refresh_html()
                else:
                    summary_path = write_summary(config.run_log_path)
                if summary_path is not None:
                    logger.info("HTML run summary written to %s", summary_path)
            except Exception:
                logger.exception("failed to write HTML run summary from %s", config.run_log_path)

    if world is not None:
        logger.info(
            "client completed: final inventory=%s, %d transaction(s)",
            world.self.inventory,
            len(world.transactions),
        )


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        configure_logging()
        logger.error("configuration error: %s", exc)
        raise SystemExit(1) from exc

    configure_logging(logging.DEBUG if config.verbose else logging.INFO)
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
