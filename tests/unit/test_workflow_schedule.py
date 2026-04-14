"""Tests for Story 6.6 — Schedule configuration and Metaflow decorator."""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest
from arrow_lake.workflow.schedule import (
    ScheduleConfig,
    build_schedule,
    validate_cron_expression,
    validate_daily_time,
)


class TestScheduleConfig:
    """Test ScheduleConfig frozen dataclass."""

    def test_defaults(self) -> None:
        config = ScheduleConfig()
        assert config.cron_expression is None
        assert config.daily_time is None
        assert config.hourly is False

    def test_cron_config(self) -> None:
        config = ScheduleConfig(cron_expression="0 2 * * 1-5")
        assert config.cron_expression == "0 2 * * 1-5"

    def test_daily_config(self) -> None:
        config = ScheduleConfig(daily_time="08:00")
        assert config.daily_time == "08:00"

    def test_hourly_config(self) -> None:
        config = ScheduleConfig(hourly=True)
        assert config.hourly is True

    def test_frozen(self) -> None:
        config = ScheduleConfig()
        with pytest.raises(AttributeError):
            config.hourly = True  # type: ignore[misc]


class TestBuildSchedule:
    """Test build_schedule decorator factory."""

    def _make_fake_metaflow(self) -> types.ModuleType:
        """Create a fake metaflow.plugins.schedule_decorator module."""
        fake_module = types.ModuleType("metaflow.plugins.schedule_decorator")

        class FakeScheduleDecorator:
            name = "schedule"
            defaults: dict[str, str] = {}  # noqa: RUF012 — test mock for Metaflow protocol

            def __init__(self) -> None:
                self._defaults = {}

            def __call__(self, cls: type) -> type:
                cls._schedule_defaults = self.defaults
                return cls

        fake_module.ScheduleDecorator = FakeScheduleDecorator
        return fake_module

    def test_daily_schedule_builds(self) -> None:
        fake_mod = self._make_fake_metaflow()
        config = ScheduleConfig(daily_time="08:00")
        with patch.dict("sys.modules", {"metaflow.plugins.schedule_decorator": fake_mod}):
            decorator = build_schedule(config)
            assert callable(decorator)

    def test_hourly_schedule_builds(self) -> None:
        fake_mod = self._make_fake_metaflow()
        config = ScheduleConfig(hourly=True)
        with patch.dict("sys.modules", {"metaflow.plugins.schedule_decorator": fake_mod}):
            decorator = build_schedule(config)
            assert callable(decorator)

    def test_cron_schedule_builds(self) -> None:
        fake_mod = self._make_fake_metaflow()
        config = ScheduleConfig(cron_expression="0 2 * * 1-5")
        with patch.dict("sys.modules", {"metaflow.plugins.schedule_decorator": fake_mod}):
            decorator = build_schedule(config)
            assert callable(decorator)

    def test_no_schedule_raises(self) -> None:
        config = ScheduleConfig()
        with pytest.raises(ValueError, match="No schedule mode"):
            build_schedule(config)

    def test_multiple_modes_raises(self) -> None:
        config = ScheduleConfig(cron_expression="0 * * * *", hourly=True)
        with pytest.raises(ValueError, match="Only one schedule mode"):
            build_schedule(config)

    def test_decorator_applies_to_class(self) -> None:
        fake_mod = self._make_fake_metaflow()

        class FakeFlow:
            pass

        config = ScheduleConfig(daily_time="08:00")
        with patch.dict("sys.modules", {"metaflow.plugins.schedule_decorator": fake_mod}):
            decorator = build_schedule(config)
            result = decorator(FakeFlow)

        assert hasattr(result, "_schedule_defaults")
        assert result._schedule_defaults == {"daily_time": "08:00"}


class TestValidateCron:
    """Test cron expression validation."""

    def test_valid_cron(self) -> None:
        validate_cron_expression("0 2 * * 1-5")

    def test_valid_cron_every_minute(self) -> None:
        validate_cron_expression("* * * * *")

    def test_invalid_field_count(self) -> None:
        with pytest.raises(ValueError, match="expected 5 fields"):
            validate_cron_expression("0 2 * *")

    def test_invalid_too_many_fields(self) -> None:
        with pytest.raises(ValueError, match="expected 5 fields"):
            validate_cron_expression("0 2 * * 1-5 extra")


class TestValidateDailyTime:
    """Test daily time validation."""

    def test_valid_time(self) -> None:
        validate_daily_time("08:00")

    def test_valid_midnight(self) -> None:
        validate_daily_time("00:00")

    def test_valid_end_of_day(self) -> None:
        validate_daily_time("23:59")

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="HH:MM"):
            validate_daily_time("8am")

    def test_invalid_hour(self) -> None:
        with pytest.raises(ValueError, match="hour must be 0-23"):
            validate_daily_time("25:00")

    def test_invalid_minute(self) -> None:
        with pytest.raises(ValueError, match="minute must be 0-59"):
            validate_daily_time("08:60")

    def test_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="integers"):
            validate_daily_time("ab:cd")


class TestWorkflowConfigScheduleField:
    """Test WorkflowConfig has schedule_cron field."""

    def test_default_none(self) -> None:
        from arrow_lake.config import WorkflowConfig

        config = WorkflowConfig()
        assert config.schedule_cron is None

    def test_from_yaml(self) -> None:
        from arrow_lake.config import WorkflowConfig

        config = WorkflowConfig(schedule_cron="0 8 * * *")
        assert config.schedule_cron == "0 8 * * *"
