from __future__ import annotations

import asyncio
import logging

from planet_charlu import session
from planet_charlu.config import ConfigError, load_config
from planet_charlu.logging_utils import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        raise SystemExit(1) from exc

    logger.info("starting Planet CharLu client: %r", config)

    try:
        asyncio.run(session.run(config))
    except KeyboardInterrupt:
        logger.info("interrupted by user")


if __name__ == "__main__":
    main()
