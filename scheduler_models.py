"""Shared data models for FoxESS scheduler orchestration."""

from dataclasses import dataclass


@dataclass
class ChargeContext:
    low_solar: bool
    soc: float | None
    pv_kw: float | None
    winter: bool


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
    radiation: float | None = None
    low_solar: bool | None = None
    skip_reason: str | None = None
