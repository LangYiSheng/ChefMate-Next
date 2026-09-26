import json
import logging
from logging.handlers import RotatingFileHandler

from data_cleaning.config import ROOT


def get_logger() -> logging.Logger:
    logger = logging.getLogger("data_cleaning")
    if not logger.handlers:
        folder = ROOT / "logs"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(folder / "pipeline.log", maxBytes=2_000_000, backupCount=4, encoding="utf-8")
        except OSError:
            # A failed disk logger must not hide the original database/import error.
            handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def log_event(event: str, recipe_id: str = "", error: Exception | None = None) -> None:
    # Do not log arbitrary exception text: network exceptions may contain credentials.
    get_logger().log(
        logging.ERROR if error else logging.INFO,
        json.dumps(
            {
                "event": event,
                "recipe_id": recipe_id,
                "error_type": type(error).__name__ if error else None,
            },
            ensure_ascii=False,
        ),
    )
