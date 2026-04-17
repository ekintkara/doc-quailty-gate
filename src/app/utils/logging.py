from __future__ import annotations

import logging
import sys
from typing import Optional

import structlog


def _websocket_processor():
    try:
        from app.web.log_stream import WebSocketLogProcessor

        return WebSocketLogProcessor()
    except Exception:

        class _Noop:
            def __call__(self, logger, method_name, event_dict):
                return event_dict

        return _Noop()


def setup_logging(level: str = "INFO", enable_websocket: bool = False) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if enable_websocket:
        processors.append(_websocket_processor())

    processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
