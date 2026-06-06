"""Arrow Lake structured logging — structlog configuration.

Configures structlog with JSON rendering for production-friendly log output.
Supports correlation_id injection for distributed tracing.

See project-context.md Rule 3: structlog JSON output with correlation_id.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(
    log_level: str = "INFO",
    correlation_id: str | None = None,
) -> None:
    """Configure structlog with JSON rendering.

    Args:
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        correlation_id: Optional trace correlation ID injected into every log entry.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,  # v1.6.0: render exceptions in JSON
        structlog.processors.UnicodeDecoder(),
    ]

    # Always clear contextvars to prevent leakage between re-configures
    structlog.contextvars.clear_contextvars()
    if correlation_id:
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog's JSON formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Remove existing handlers to avoid duplicates on re-configure
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Optional logger name (defaults to root logger).

    Returns:
        A structlog BoundLogger with JSON rendering.
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
