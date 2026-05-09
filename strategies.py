"""
Charging strategies for Tauron G13s tariff.

Each class represents one season+day combination and encapsulates:
  - charge window times (dynamic based on ChargeContext)
  - whether each window should be active
  - SOC targets (with cloud bonus applied)

All numeric values come from config.py — strategies contain only logic.

Window sizing rationale (9.4 kWh battery, 10% floor = 8.46 kWh usable):
  - Grid charge rate to battery: ~5.63 kW (rest covers house load)
  - Clear days: solar contributes during/after window → shorter window ok
  - Cloudy days: no solar help → need full time based on 5.63 kW rate

Static strategies use fixed windows based on low_solar flag.
Dynamic strategies (g13s_dynamic tariff) calculate window start at
runtime from current SOC and PV output for maximum accuracy.
"""
import datetime
from abc import ABC, abstractmethod
import config as cfg
from context import ChargeContext


class ChargeStrategy(ABC):
    """Base class for all charging strategies."""

    name: str

    # Window 1: morning top-up. Use get_window1(ctx).
    window1:           tuple[str, str]   # clear day
    window1_low_solar: tuple[str, str]   # cloudy day — starts earlier

    # Window 2: pre-evening top-up. Use get_window2(ctx).
    window2:           tuple[str, str]   # clear day
    window2_low_solar: tuple[str, str]   # cloudy day — starts earlier

    @abstractmethod
    def enable1(self) -> bool:
        """Whether window 1 should be considered at all today."""

    @abstractmethod
    def enable2(self) -> bool:
        """Whether window 2 should be considered at all today."""

    @abstractmethod
    def morning_target(self, ctx: ChargeContext) -> int:
        """SOC% threshold below which window 1 activates."""

    @abstractmethod
    def evening_target(self, ctx: ChargeContext) -> int:
        """SOC% threshold below which window 2 activates."""

    def get_window1(self, ctx: ChargeContext) -> tuple[str, str]:
        """Return window 1 times based on context."""
        return self.window1_low_solar if ctx.low_solar else self.window1

    def get_window2(self, ctx: ChargeContext) -> tuple[str, str]:
        """Return window 2 times based on context."""
        return self.window2_low_solar if ctx.low_solar else self.window2

    def _m(self, base: int, ctx: ChargeContext) -> int:
        bonus = cfg.CLOUD_BONUS_MORNING if ctx.low_solar else 0
        return min(base + bonus, 100)

    def _e(self, base: int, ctx: ChargeContext) -> int:
        bonus = cfg.CLOUD_BONUS_EVENING if ctx.low_solar else 0
        return min(base + bonus, 100)


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
    # Cloudy: 90 min — worst case 10%→100% at 5.63kW needs ~90 min
    window2           = ("16:20", "17:00")
    window2_low_solar = ("15:30", "17:00")

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, ctx): return self._m(cfg.TARGET_SUMMER_WEEKDAY_MORNING, ctx)
    def evening_target(self, ctx): return self._e(cfg.TARGET_SUMMER_WEEKDAY_EVENING, ctx)


class SummerWeekend(ChargeStrategy):
    name              = "G13s SUMMER weekend"
    window1           = ("06:50", "07:00")   # disabled — no peak on weekends
    window1_low_solar = ("06:50", "07:00")   # disabled — no peak on weekends
    window2           = ("16:20", "17:00")   # disabled by default
    window2_low_solar = ("15:30", "17:00")

    def enable1(self): return False
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY

    # morning_target fixed at minimum — window is disabled, 10% is FoxESS system minimum
    def morning_target(self, ctx): return cfg.TARGET_SUMMER_WEEKEND_MORNING
    def evening_target(self, ctx): return self._e(cfg.TARGET_SUMMER_WEEKEND_EVENING, ctx)


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
    window1           = ("06:30", "07:00")
    window1_low_solar = ("06:30", "07:00")
    window2           = ("14:20", "15:00")
    window2_low_solar = ("13:00", "15:00")

    def enable1(self): return True
    def enable2(self): return True

    def morning_target(self, ctx): return self._m(cfg.TARGET_WINTER_WEEKDAY_MORNING, ctx)
    def evening_target(self, ctx): return self._e(cfg.TARGET_WINTER_WEEKDAY_EVENING, ctx)


class WinterWeekend(ChargeStrategy):
    name              = "G13s WINTER weekend"
    window1           = ("06:30", "07:00")
    window1_low_solar = ("06:30", "07:00")
    window2           = ("14:20", "15:00")
    window2_low_solar = ("13:00", "15:00")

    def enable1(self): return False
    def enable2(self): return cfg.G13S_WEEKEND_MIDDAY

    def morning_target(self, ctx): return self._m(cfg.TARGET_WINTER_WEEKEND_MORNING, ctx)
    def evening_target(self, ctx): return self._e(cfg.TARGET_WINTER_WEEKEND_EVENING, ctx)


# ── Dynamic strategies (g13s_dynamic tariff) ─────────────────────────────────
# Inherit static windows as fallback, override get_window2() to calculate
# start time dynamically from current SOC and PV output.

def _dynamic_window2_start(ctx: ChargeContext, end_time: str, target: int) -> str:
    """Calculate window 2 start time based on current SOC and PV output.

    Uses actual battery charge rate accounting for PV contribution:
      net_rate = BATTERY_CHARGE_RATE - pv_kw  (PV covers house load)
      minutes  = (target - soc) × BATTERY_KWH / net_rate × 60 / 100
      start    = end_time - minutes × SAFETY_MARGIN

    Falls back to static window if SOC/PV unavailable.
    """
    if ctx.soc is None or ctx.pv_kw is None:
        return None  # signal to use static fallback

    soc_needed = max(target - ctx.soc, 0)
    if soc_needed <= 0:
        return None  # already at target, static window fine

    # Net charge rate to battery — PV offsets house load, not directly battery
    net_rate = max(cfg.BATTERY_CHARGE_RATE_KW - ctx.pv_kw, 1.0)
    minutes  = soc_needed * cfg.BATTERY_KWH / net_rate * 60 / 100
    minutes *= cfg.CHARGE_SAFETY_MARGIN

    h, m   = map(int, end_time.split(":"))
    end_dt = datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    start_dt = end_dt - datetime.timedelta(minutes=int(minutes))

    # Don't start before 06:00 to avoid charging during night rate
    earliest = end_dt.replace(hour=6, minute=0)
    if start_dt < earliest:
        start_dt = earliest

    result = start_dt.strftime("%H:%M")
    print(f"  Dynamic : SOC={ctx.soc:.0f}%  PV={ctx.pv_kw:.2f}kW  "
          f"net_rate={net_rate:.2f}kW  need={minutes:.0f}min  start={result}")
    return result


class DynamicSummerWeekday(SummerWeekday):
    name = "G13s DYNAMIC SUMMER weekday"

    def get_window2(self, ctx: ChargeContext) -> tuple[str, str]:
        end    = "17:00"
        target = self.evening_target(ctx)
        start  = _dynamic_window2_start(ctx, end, target)
        if start is None:
            return super().get_window2(ctx)
        return start, end


class DynamicSummerWeekend(SummerWeekend):
    name = "G13s DYNAMIC SUMMER weekend"

    def get_window2(self, ctx: ChargeContext) -> tuple[str, str]:
        end    = "17:00"
        target = self.evening_target(ctx)
        start  = _dynamic_window2_start(ctx, end, target)
        if start is None:
            return super().get_window2(ctx)
        return start, end


class DynamicWinterWeekday(WinterWeekday):
    name = "G13s DYNAMIC WINTER weekday"

    def get_window2(self, ctx: ChargeContext) -> tuple[str, str]:
        end    = "15:00"
        target = self.evening_target(ctx)
        start  = _dynamic_window2_start(ctx, end, target)
        if start is None:
            return super().get_window2(ctx)
        return start, end


class DynamicWinterWeekend(WinterWeekend):
    name = "G13s DYNAMIC WINTER weekend"

    def get_window2(self, ctx: ChargeContext) -> tuple[str, str]:
        end    = "15:00"
        target = self.evening_target(ctx)
        start  = _dynamic_window2_start(ctx, end, target)
        if start is None:
            return super().get_window2(ctx)
        return start, end


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
    def morning_target(self, ctx): return 100
    def evening_target(self, ctx): return 100


# ── Factory ───────────────────────────────────────────────────────────────────

def get_strategy(date: datetime.date, tariff: str) -> ChargeStrategy:
    """Return the appropriate strategy for the given date and tariff."""
    is_weekday = date.weekday() < 5
    dynamic    = tariff == "g13s_dynamic"

    if tariff == "manual":
        return ManualStrategy(is_weekday)

    winter = date.month >= 10 or date.month <= 3
    if winter:
        if is_weekday:
            return DynamicWinterWeekday() if dynamic else WinterWeekday()
        else:
            return DynamicWinterWeekend() if dynamic else WinterWeekend()
    else:
        if is_weekday:
            return DynamicSummerWeekday() if dynamic else SummerWeekday()
        else:
            return DynamicSummerWeekend() if dynamic else SummerWeekend()
