from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import structlog
from logging.handlers import RotatingFileHandler

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOG_DIR = _PROJECT_ROOT / "logs"

_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 3


class LevelFilter(logging.Filter):
    def __init__(self, pass_levels: list[int]):
        super().__init__()
        self._pass_levels = set(pass_levels)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in self._pass_levels


def _ensure_log_dir(log_dir: Optional[str] = None) -> Path:
    target = Path(log_dir) if log_dir else _LOG_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _create_file_handler(
    log_dir: Path,
    filename: str,
    level: int,
    formatter: logging.Formatter,
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        log_dir / filename,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def setup_logging(
    level: str = "INFO",
    enable_websocket: bool = False,
    log_dir: Optional[str] = None,
) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    target_dir = _ensure_log_dir(log_dir)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    all_handler = _create_file_handler(target_dir, "dqg.log", log_level, formatter)
    info_handler = _create_file_handler(target_dir, "dqg_info.log", logging.INFO, formatter)
    info_handler.addFilter(LevelFilter([logging.INFO]))

    warning_handler = _create_file_handler(target_dir, "dqg_warning.log", logging.WARNING, formatter)
    warning_handler.addFilter(LevelFilter([logging.WARNING]))

    error_handler = _create_file_handler(target_dir, "dqg_error.log", logging.ERROR, formatter)
    error_handler.addFilter(LevelFilter([logging.ERROR, logging.CRITICAL]))

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    stdlib_logger = logging.getLogger()
    stdlib_logger.setLevel(logging.DEBUG)
    stdlib_logger.handlers.clear()
    stdlib_logger.addHandler(console_handler)
    stdlib_logger.addHandler(all_handler)
    stdlib_logger.addHandler(info_handler)
    stdlib_logger.addHandler(warning_handler)
    stdlib_logger.addHandler(error_handler)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.dev.set_exc_info,
    ]

    if enable_websocket:
        try:
            from app.web.log_stream import WebSocketLogProcessor

            shared_processors.append(WebSocketLogProcessor())
        except Exception:
            pass

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    for noisy in ["httpx", "httpcore", "uvicorn.access"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
