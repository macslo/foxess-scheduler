"""
Charging strategies for Tauron G13s tariff.

Each class represents one season+day combination and encapsulates:
  - charge window times
  - whether each window should be active
  - SOC targets (with cloud bonus applied)

All numeric values come from config.py — strategies contain only logic.
"""
import datetime
from abc import ABC, abstractmethod
import config as cfg


class ChargeStrategy(ABC):
    """Base class for all charging strategies."""

    name: str           # human-readable label shown in logs

    # Window 1: morning top-up
    window1: tuple[str, str]

    # Window 2: pre-evening top-up
    window2: tuple[str, str]

    @abstractmethod
    def enable1(self) -> bool:
        """Whether window 1 should be considered at all today."""

    @abstractmethod
    def enable2(self) -> bool:
        """Whether window 2 should be considered at all today."""

    @abstractmethod
    def morning_target(self, low_solar: bool) -> int:
        """SOC% threshold below which window 1 activates."""

    @abstractmethod
    def evening_target(self, low_solar: bool) -> int:
        """SOC% threshold below which window 2 activates."""

    def _m(self, base: int, low_solar: bool) -> int:
        bonus = cfg.CLOUD_BONUS_MORNING if low_solar else 0
        return min(base + bonus, 95)

    def _e(self, base: int, low_solar: bool) -> int:
        bonus = cfg.CLOUD_BONUS_EVENING if low_solar else 0
        return min(base + bonus, 95)


# ── Summer strategies (1 Apr – 30 Sep) ───────────────────────────────────────
#
# Weekdays:
#   07:00–09:00  🔴 PEAK  (1.2219 zł/kWh) ← avoid
#   09:00–17:00  🟢 cheap (0.4613 zł/kWh) ← solar hours
#   17:00–21:00  🔴 PEAK  (1.2219 zł/kWh) ← avoid
#
# Weekends — NO true peak:
#   07:00–09:00  🟡 neutral (0.4972 zł/kWh)
#   09:00–17:00  🟢 very cheap (0.1882 zł/kWh)
#   17:00–21:00  🟡 neutral (0.4972 zł/kWh)

class SummerWeekday(ChargeStrategy):
    name     = "G13s SUMMER weekday"
    window1  = ("06:30", "07:00")   # top up before 07:00 morning peak
    window2  = ("15:30", "17:00")   # top up before 17:00 evening peak

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, low_solar): return self._m(cfg.TARGET_SUMMER_WEEKDAY_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_SUMMER_WEEKDAY_EVENING, low_solar)


class SummerWeekend(ChargeStrategy):
    name     = "G13s SUMMER weekend"
    window1  = ("06:30", "07:00")   # optional — no peak, but dirt-cheap night rate ending
    window2  = ("15:30", "17:00")   # optional — no peak, but goal is full battery by night

    def enable1(self): return False                       # no morning peak on weekends
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY    # off by default, configurable

    def morning_target(self, low_solar): return self._m(cfg.TARGET_SUMMER_WEEKEND_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_SUMMER_WEEKEND_EVENING, low_solar)


# ── Winter strategies (1 Oct – 31 Mar) ───────────────────────────────────────
#
# Weekdays:
#   07:00–10:00  🔴 PEAK  (1.2821 zł/kWh) ← avoid
#   10:00–15:00  🟡 cheap (0.9286 zł/kWh)
#   15:00–21:00  🔴 PEAK  (1.2821 zł/kWh) ← avoid — 6h! hardest block
#
# Weekends — NO true peak:
#   07:00–10:00  🟡 neutral (0.7669 zł/kWh)
#   10:00–15:00  🟢 cheap  (0.5597 zł/kWh)
#   15:00–21:00  🟡 neutral (0.7669 zł/kWh)

class WinterWeekday(ChargeStrategy):
    name     = "G13s WINTER weekday"
    window1  = ("06:30", "07:00")   # top up before 07:00 morning peak (3h)
    window2  = ("13:30", "15:00")   # top up before 15:00 evening peak (6h — worst block)

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, low_solar): return self._m(cfg.TARGET_WINTER_WEEKDAY_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_WINTER_WEEKDAY_EVENING, low_solar)


class WinterWeekend(ChargeStrategy):
    name     = "G13s WINTER weekend"
    window1  = ("06:30", "07:00")   # no peak but heading into neutral rate
    window2  = ("13:30", "15:00")   # no peak but goal is full battery by night

    def enable1(self): return False                       # no morning peak on weekends
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY    # off by default, configurable

    def morning_target(self, low_solar): return self._m(cfg.TARGET_WINTER_WEEKEND_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_WINTER_WEEKEND_EVENING, low_solar)


# ── Manual strategy ───────────────────────────────────────────────────────────

class ManualStrategy(ChargeStrategy):
    """User-defined windows from config. No SOC targets — windows follow policy only."""

    def __init__(self, is_weekday: bool):
        self.name    = f"Manual ({'weekday' if is_weekday else 'weekend'})"
        self.window1 = (cfg.CHARGE1_START, cfg.CHARGE1_END)
        self.window2 = (cfg.CHARGE2_START, cfg.CHARGE2_END)
        self._enable1 = self._resolve(cfg.CHARGE1_ENABLE, is_weekday)
        self._enable2 = self._resolve(cfg.CHARGE2_ENABLE, is_weekday)

    @staticmethod
    def _resolve(policy: str, is_weekday: bool) -> bool:
        p = policy.strip().lower()
        if p == "always":   return True
        if p == "never":    return False
        if p == "weekdays": return is_weekday
        if p == "weekends": return not is_weekday
        print(f"  Warning: unknown policy '{p}', defaulting to never")
        return False

    def enable1(self): return self._enable1
    def enable2(self): return self._enable2

    # Manual mode uses no SOC gating — return 100 so condition is never triggered
    def morning_target(self, low_solar): return 100
    def evening_target(self, low_solar): return 100


# ── Factory ───────────────────────────────────────────────────────────────────

def get_strategy(date: datetime.date, tariff: str) -> ChargeStrategy:
    """Return the appropriate strategy for the given date and tariff."""
    is_weekday = date.weekday() < 5

    if tariff == "manual":
        return ManualStrategy(is_weekday)

    # G13s (default)
    winter = date.month >= 10 or date.month <= 3
    if winter:
        return WinterWeekday() if is_weekday else WinterWeekend()
    else:
        return SummerWeekday() if is_weekday else SummerWeekend()
