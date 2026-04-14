"""Schedule configuration for Metaflow cron pipelines (Story 6.6).

Provides typed schedule configuration and a builder for Metaflow's
``@schedule`` StepDecorator. Supports daily, hourly, and cron expressions.

Usage::

    from arrow_lake.workflow.schedule import ScheduleConfig, build_schedule

    config = ScheduleConfig(daily_time="08:00")
    schedule_decorator = build_schedule(config)

    @schedule_decorator
    @step
    def my_step(self):
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduleConfig:
    """Schedule configuration for a Metaflow flow.

    Attributes:
        cron_expression: Cron expression (e.g. ``"0 2 * * 1-5"``).
        daily_time: Daily time in HH:MM format (e.g. ``"08:00"``).
        hourly: Run every hour.
    """

    cron_expression: str | None = None
    daily_time: str | None = None
    hourly: bool = False


def build_schedule(config: ScheduleConfig) -> Any:
    """Build a Metaflow ``@schedule`` decorator from a ScheduleConfig.

    Wraps ``metaflow.plugins.schedule_decorator.ScheduleDecorator``
    following the same pattern as ``build_metaflow_retry``.

    Args:
        config: Schedule configuration.

    Returns:
        Decorator function for use on FlowSpec classes.

    Raises:
        ValueError: If no schedule mode is specified.
        ValueError: If multiple schedule modes are specified.
    """
    modes = [
        config.cron_expression is not None,
        config.daily_time is not None,
        config.hourly,
    ]
    active_count = sum(modes)

    if active_count == 0:
        raise ValueError("No schedule mode specified; set cron_expression, daily_time, or hourly")
    if active_count > 1:
        raise ValueError("Only one schedule mode can be active at a time")

    from metaflow.plugins.schedule_decorator import (
        ScheduleDecorator,  # type: ignore[import-untyped]
    )

    if config.cron_expression is not None:
        schedule_defaults = {"cron": config.cron_expression}
    elif config.daily_time is not None:
        schedule_defaults = {"daily_time": config.daily_time}
    else:
        schedule_defaults = {"hourly": "True"}

    configured_schedule_cls = type(
        "ConfiguredSchedule",
        (ScheduleDecorator,),
        {"name": "schedule", "defaults": schedule_defaults},
    )

    def _decorator(cls: type[Any]) -> type[Any]:
        return configured_schedule_cls()(cls)

    return _decorator


def validate_cron_expression(expr: str) -> None:
    """Validate a cron expression has the expected 5 fields.

    Args:
        expr: Cron expression to validate.

    Raises:
        ValueError: If the expression has an invalid number of fields.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Invalid cron expression '{expr}': expected 5 fields "
            f"(minute hour day_of_month month day_of_week), got {len(fields)}"
        )


def validate_daily_time(time_str: str) -> None:
    """Validate a daily time string is in HH:MM format.

    Args:
        time_str: Time string to validate.

    Raises:
        ValueError: If the format is invalid.
    """
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid daily_time '{time_str}': expected HH:MM format")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f"Invalid daily_time '{time_str}': hour and minute must be integers"
        ) from None
    if not (0 <= hour <= 23):
        raise ValueError(f"Invalid daily_time '{time_str}': hour must be 0-23")
    if not (0 <= minute <= 59):
        raise ValueError(f"Invalid daily_time '{time_str}': minute must be 0-59")
