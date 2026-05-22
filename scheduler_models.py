"""Shared data models for FoxESS scheduler orchestration."""

from dataclasses import dataclass


@dataclass
class ChargeWindow:
    start: str
    end: str
    enabled: bool | None


@dataclass
class ChargePlan:
    window1: ChargeWindow
    window2: ChargeWindow
    morning_target: int
    evening_target: int


@dataclass
class ProximityResult:
    should_run: bool
    radiation: int | None = None
    low_solar: bool | None = None
    skip_reason: str | None = None
