"""
Charging strategies for Tauron G13s tariff.

Each class represents one season+day combination and encapsulates:
  - charge window times (dynamic based on solar forecast)
  - whether each window should be active
  - SOC targets (with cloud bonus applied)

All numeric values come from config.py — strategies contain only logic.

Window sizing rationale (9.4 kWh battery, 10% floor = 8.46 kWh usable):
  - Grid charge rate to battery: ~5.63 kW (rest covers house load)
  - Clear days: solar contributes during/after window → shorter window ok
  - Cloudy days: no solar help → need full time based on 5.63 kW rate
"""
import datetime
from abc import ABC, abstractmethod
import config as cfg


class ChargeStrategy(ABC):
    """Base class for all charging strategies."""

    name: str

    # Window 1: morning top-up. Use get_window1(low_solar) — not window1 directly.
    window1:           tuple[str, str]   # clear day
    window1_low_solar: tuple[str, str]   # cloudy day — starts earlier

    # Window 2: pre-evening top-up. Use get_window2(low_solar) — not window2 directly.
    window2:           tuple[str, str]   # clear day
    window2_low_solar: tuple[str, str]   # cloudy day — starts earlier

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

    def get_window1(self, low_solar: bool) -> tuple[str, str]:
        """Return window 1 times based on solar forecast.

        Clear day: solar arrives ~08:00, only 1h peak to cover → short window.
        Cloudy day: no solar help, need a few extra minutes at 5.63 kW.
        """
        return self.window1_low_solar if low_solar else self.window1

    def get_window2(self, low_solar: bool) -> tuple[str, str]:
        """Return window 2 times based on solar forecast.

        Clear day: solar still contributing → 40 min enough.
        Cloudy day: worst case 10%→85% at 5.63 kW needs ~75 min.
        """
        return self.window2_low_solar if low_solar else self.window2

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
    name = "G13s SUMMER weekday"
    # Window 1: cover 07:00-09:00 peak (2h, ~1kW usage)
    # Clear:  10 min — solar arrives ~08:00, worst case 10%→25% in ~8 min at 5.63kW
    # Cloudy: 15 min — no solar, same worst case needs ~15 min at 5.63kW
    window1           = ("06:50", "07:00")
    window1_low_solar = ("06:45", "07:00")
    # Window 2: cover 17:00-21:00 peak (4h)
    # Clear:  40 min — solar still contributing at 16:20
    # Cloudy: 90 min — worst case 10%→95% at 5.63kW needs ~78 min, 12 min margin
    window2           = ("16:20", "17:00")
    window2_low_solar = ("15:30", "17:00")

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, low_solar): return self._m(cfg.TARGET_SUMMER_WEEKDAY_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_SUMMER_WEEKDAY_EVENING, low_solar)


class SummerWeekend(ChargeStrategy):
    name              = "G13s SUMMER weekend"
    window1           = ("06:50", "07:00")   # disabled by default — no peak on weekends
    window1_low_solar = ("06:45", "07:00")
    window2           = ("16:20", "17:00")   # disabled by default
    window2_low_solar = ("15:30", "17:00")

    def enable1(self): return False
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY

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
    name = "G13s WINTER weekday"
    # Window 1: cover 07:00-10:00 peak (3h)
    # Same for clear/cloudy — winter solar negligible regardless
    window1           = ("06:30", "07:00")   # 30 min
    window1_low_solar = ("06:30", "07:00")   # same — solar minimal in winter mornings
    # Window 2: cover 15:00-21:00 peak (6h — worst block)
    # Clear:  40 min
    # Cloudy: 120 min — worst case 10%→95% needs ~75 min, extra margin for cold
    window2           = ("14:20", "15:00")
    window2_low_solar = ("13:00", "15:00")

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, low_solar): return self._m(cfg.TARGET_WINTER_WEEKDAY_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_WINTER_WEEKDAY_EVENING, low_solar)


class WinterWeekend(ChargeStrategy):
    name              = "G13s WINTER weekend"
    window1           = ("06:30", "07:00")   # disabled by default — no peak on weekends
    window1_low_solar = ("06:30", "07:00")
    window2           = ("14:20", "15:00")   # disabled by default
    window2_low_solar = ("13:00", "15:00")

    def enable1(self): return False
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY

    def morning_target(self, low_solar): return self._m(cfg.TARGET_WINTER_WEEKEND_MORNING, low_solar)
    def evening_target(self, low_solar): return self._e(cfg.TARGET_WINTER_WEEKEND_EVENING, low_solar)


# ── Manual strategy ───────────────────────────────────────────────────────────

class ManualStrategy(ChargeStrategy):
    """User-defined windows from config. No SOC targets — windows follow policy only."""

    def __init__(self, is_weekday: bool):
        self.name              = f"Manual ({'weekday' if is_weekday else 'weekend'})"
        self.window1           = (cfg.CHARGE1_START, cfg.CHARGE1_END)
        self.window1_low_solar = (cfg.CHARGE1_START, cfg.CHARGE1_END)
        self.window2           = (cfg.CHARGE2_START, cfg.CHARGE2_END)
        self.window2_low_solar = (cfg.CHARGE2_START, cfg.CHARGE2_END)
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
    def morning_target(self, low_solar): return 100
    def evening_target(self, low_solar): return 100


# ── Factory ───────────────────────────────────────────────────────────────────

def get_strategy(date: datetime.date, tariff: str) -> ChargeStrategy:
    """Return the appropriate strategy for the given date and tariff."""
    is_weekday = date.weekday() < 5

    if tariff == "manual":
        return ManualStrategy(is_weekday)

    winter = date.month >= 10 or date.month <= 3
    if winter:
        return WinterWeekday() if is_weekday else WinterWeekend()
    else:
        return SummerWeekday() if is_weekday else SummerWeekend()
