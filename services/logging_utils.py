from __future__ import annotations

import logging
import os


def get_logger(name: str) -> logging.Logger:
    level_name = os.getenv("MARKET_BRIEF_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, level_name, logging.INFO),
    )
    return logging.getLogger(name)
