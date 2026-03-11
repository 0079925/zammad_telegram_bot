"""
Structured logging setup via structlog.

Features:
- JSON output in production, colored console in development
- Automatic correlation_id propagation from context
- Sensitive fields (tokens, phones) are masked in log output
- All log records include environment, service name
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

_MASKED_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "http_token",
        "bot_token",
        "authorization",
        "phone",
        "phone_number",
    }
)


def _mask_sensitive(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Replace sensitive values with '***MASKED***'."""
    for key in list(event_dict.keys()):
        if any(k in key.lower() for k in _MASKED_KEYS):
            event_dict[key] = "***MASKED***"
    return event_dict


def configure_logging(log_level: str = "INFO", is_development: bool = False) -> None:
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _mask_sensitive,
        structlog.processors.StackInfoRenderer(),
    ]

    if is_development:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level.upper())

    # Quiet noisy third-party loggers
    for name in ("httpx", "httpcore", "aiogram.event"):
        logging.getLogger(name).setLevel(logging.WARNING)
