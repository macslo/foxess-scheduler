"""
Unit tests for FoxESS Grid Charge Scheduler.

Run with:
    python3 -m pytest test_scheduler.py -v
or:
    python3 test_scheduler.py
"""
import datetime
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from strategies import (
    get_strategy,
    SummerWeekday, SummerWeekend,
    WinterWeekday, WinterWeekend,
    DynamicSummerWeekday, DynamicSummerWeekend,
    DynamicWinterWeekday, DynamicWinterWeekend,
    ManualStrategy,
)
from scheduler_models import ChargeContext
from scheduler_models import ChargePlan, ChargeWindow
from weather import is_low_solar, SOLAR_GOOD, SOLAR_POOR
import windows
import config as cfg
from unittest.mock import patch
import strategies as _strategies_module
import foxess_grid_charge_scheduler as _scheduler_module
from proximity import saved_window_relevant, window_in_progress, proximity_check


class patch_hour:
    """Context manager to mock datetime.datetime.now().hour in strategies."""
    def __init__(self, hour):
        self.hour = hour
        self._patcher = None

    def __enter__(self):
        fixed = datetime.datetime.now().replace(hour=self.hour, minute=0)
        self._patcher = patch.object(_strategies_module.datetime, 'datetime',
                                     wraps=datetime.datetime)
        mock_dt = self._patcher.start()
        mock_dt.now.return_value = fixed
        return self

    def __exit__(self, *args):
        self._patcher.stop()


def dt(h, m=0):
    return datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)

def date(year, month, day):
    return datetime.date(year, month, day)

def ctx(low_solar=False, soc=50.0, pv_kw=0.0, winter=False):
    return ChargeContext(low_solar=low_solar, soc=soc, pv_kw=pv_kw, winter=winter)


# ── Strategy selection ────────────────────────────────────────────────────────
class TestGetStrategy(unittest.TestCase):

    def test_summer_weekday(self):
        self.assertIsInstance(get_strategy(date(2025, 5, 5), "g13s"), SummerWeekday)

    def test_summer_weekend(self):
        self.assertIsInstance(get_strategy(date(2025, 5, 10), "g13s"), SummerWeekend)

    def test_winter_weekday(self):
        self.assertIsInstance(get_strategy(date(2025, 11, 3), "g13s"), WinterWeekday)

    def test_winter_weekend(self):
        self.assertIsInstance(get_strategy(date(2025, 11, 2), "g13s"), WinterWeekend)

    def test_dynamic_summer_weekday(self):
        self.assertIsInstance(get_strategy(date(2025, 5, 5), "g13s_dynamic"), DynamicSummerWeekday)

    def test_dynamic_winter_weekday(self):
        self.assertIsInstance(get_strategy(date(2025, 11, 3), "g13s_dynamic"), DynamicWinterWeekday)

    def test_winter_boundary_october(self):
        s = get_strategy(date(2025, 10, 1), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_winter_boundary_march(self):
        s = get_strategy(date(2025, 3, 31), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_summer_boundary_april(self):
        s = get_strategy(date(2025, 4, 1), "g13s")
        self.assertIsInstance(s, (SummerWeekday, SummerWeekend))

    def test_manual_weekday(self):
        s = get_strategy(date(2025, 5, 5), "manual")
        self.assertIsInstance(s, ManualStrategy)
        self.assertIn("weekday", s.name)


# ── Strategy enable logic ─────────────────────────────────────────────────────
class TestStrategyEnableLogic(unittest.TestCase):

    def test_summer_weekday_both_enabled(self):
        s = SummerWeekday()
        self.assertTrue(s.enable1())
        self.assertTrue(s.enable2())

    def test_summer_weekend_window1_disabled(self):
        """enable1 should be False on Saturday (not Sunday evening)."""
        s = SummerWeekend()
        # Use Saturday to avoid triggering Sunday evening logic
        saturday = datetime.date(2025, 5, 10)  # known Saturday
        fixed    = datetime.datetime.combine(saturday, datetime.time(10, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertFalse(s.enable1())

    def test_summer_weekend_window2_follows_config(self):
        self.assertEqual(SummerWeekend().enable2(), cfg.G13S_WEEKEND_MIDDAY)

    def test_winter_weekday_both_enabled(self):
        s = WinterWeekday()
        self.assertTrue(s.enable1())
        self.assertTrue(s.enable2())

    def test_winter_weekend_window1_disabled(self):
        self.assertFalse(WinterWeekend().enable1())


# ── Static window times ───────────────────────────────────────────────────────
class TestStrategyWindowTimes(unittest.TestCase):

    def test_summer_weekday_window1_clear(self):
        self.assertEqual(SummerWeekday().get_window1(ctx(False)), ("06:50", "07:00"))

    def test_summer_weekday_window1_cloudy(self):
        self.assertEqual(SummerWeekday().get_window1(ctx(True)), ("06:45", "07:00"))

    def test_summer_weekday_window2_clear(self):
        self.assertEqual(SummerWeekday().get_window2(ctx(False)), ("16:20", "17:00"))

    def test_summer_weekday_window2_cloudy(self):
        self.assertEqual(SummerWeekday().get_window2(ctx(True)), ("15:30", "17:00"))

    def test_winter_weekday_window1_same_both(self):
        s = WinterWeekday()
        self.assertEqual(s.get_window1(ctx(False)), s.get_window1(ctx(True)))

    def test_winter_weekday_window2_clear(self):
        self.assertEqual(WinterWeekday().get_window2(ctx(False)), ("14:20", "15:00"))

    def test_winter_weekday_window2_cloudy(self):
        self.assertEqual(WinterWeekday().get_window2(ctx(True)), ("13:00", "15:00"))

    def test_cloudy_window1_starts_same_or_earlier(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertLessEqual(s.get_window1(ctx(True))[0], s.get_window1(ctx(False))[0])

    def test_cloudy_window2_starts_earlier(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertLessEqual(s.get_window2(ctx(True))[0], s.get_window2(ctx(False))[0])


# ── Dynamic window times ──────────────────────────────────────────────────────
class TestDynamicWindowTimes(unittest.TestCase):

    # ── Time guard ────────────────────────────────────────────────────────────

    def test_summer_before_13_uses_static_cloudy(self):
        """Before 13:00 dynamic should fall back to static cloudy window."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5)
        with patch_hour(12):
            self.assertEqual(s.get_window2(c), ("15:30", "17:00"))

    def test_summer_before_13_uses_static_clear(self):
        """Before 13:00 dynamic should fall back to static clear window."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=20.0, pv_kw=2.0)
        with patch_hour(8):
            self.assertEqual(s.get_window2(c), ("16:20", "17:00"))

    def test_summer_after_13_uses_dynamic(self):
        """After 13:00 with full context should return dynamic start."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5)
        with patch_hour(14):
            start, end = s.get_window2(c)
            self.assertEqual(end, "17:00")
            self.assertNotEqual(start, "15:30")   # not static
            self.assertNotEqual(start, "16:20")   # not static

    def test_winter_before_10_uses_static(self):
        """Before 10:00 winter dynamic should fall back to static."""
        s = DynamicWinterWeekday()
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5, winter=True)
        with patch_hour(9):
            self.assertEqual(s.get_window2(c), ("13:00", "15:00"))

    def test_winter_after_10_uses_dynamic(self):
        """After 10:00 winter dynamic should calculate start."""
        s = DynamicWinterWeekday()
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5, winter=True)
        with patch_hour(11):
            start, end = s.get_window2(c)
            self.assertEqual(end, "15:00")
            self.assertNotEqual(start, "13:00")   # not static

    # ── Fallback conditions ───────────────────────────────────────────────────

    def test_falls_back_when_soc_none(self):
        s = DynamicSummerWeekday()
        c = ChargeContext(low_solar=True, soc=None, pv_kw=0.5, winter=False)
        with patch_hour(14):
            self.assertEqual(s.get_window2(c), ("15:30", "17:00"))

    def test_falls_back_when_pv_none(self):
        s = DynamicSummerWeekday()
        c = ChargeContext(low_solar=True, soc=20.0, pv_kw=None, winter=False)
        with patch_hour(14):
            self.assertEqual(s.get_window2(c), ("15:30", "17:00"))

    def test_falls_back_when_soc_at_target(self):
        """SOC already at or above target — no charging needed → static."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=99.0, pv_kw=3.0)
        with patch_hour(14):
            self.assertEqual(s.get_window2(c), ("16:20", "17:00"))

    # ── Realistic scenarios ───────────────────────────────────────────────────

    def test_low_soc_low_pv_starts_early(self):
        """Low SOC + low PV → long charge needed → early start."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=True, soc=10.0, pv_kw=0.1)
        with patch_hour(14):
            start, _ = s.get_window2(c)
            # Should start well before 16:20
            h, m = map(int, start.split(":"))
            self.assertLess(h * 60 + m, 16 * 60 + 20)

    def test_high_soc_starts_late(self):
        """High SOC → little charging needed → late start."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=75.0, pv_kw=1.0)
        with patch_hour(14):
            start, _ = s.get_window2(c)
            h, m = map(int, start.split(":"))
            # Should start close to 16:20 or later
            self.assertGreaterEqual(h * 60 + m, 15 * 60 + 30)

    def test_high_pv_starts_earlier_than_low_pv(self):
        """High PV reduces net charge rate → needs more time → earlier start."""
        s      = DynamicSummerWeekday()
        low_pv = ctx(low_solar=True, soc=20.0, pv_kw=0.5)
        hi_pv  = ctx(low_solar=True, soc=20.0, pv_kw=4.0)
        with patch_hour(14):
            start_low, _ = s.get_window2(low_pv)
            start_hi,  _ = s.get_window2(hi_pv)
            self.assertLessEqual(start_hi, start_low)

    def test_today_scenario_soc10_pv056(self):
        """Replays real data: SOC 10%, PV 0.56 kW, should start before 15:30."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=True, soc=10.0, pv_kw=0.56)
        with patch_hour(14):
            start, end = s.get_window2(c)
            self.assertEqual(end, "17:00")
            h, m = map(int, start.split(":"))
            # Real scenario needed ~101 min → start ~15:09 — must be before 15:30
            self.assertLess(h * 60 + m, 15 * 60 + 30)

    # ── Boundary / edge cases ─────────────────────────────────────────────────

    def test_start_never_before_0600(self):
        """Very low SOC + very low PV should not start before 06:00."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=True, soc=1.0, pv_kw=0.01)
        with patch_hour(14):
            start, _ = s.get_window2(c)
            h, m = map(int, start.split(":"))
            self.assertGreaterEqual(h * 60 + m, 6 * 60)

    def test_dynamic_weekend_disabled_enable2_false(self):
        """Weekend dynamic — enable2=False so window doesn't matter, but
        get_window2 should still return a valid tuple."""
        s = DynamicSummerWeekend()
        self.assertFalse(s.enable2())   # window is off
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5)
        with patch_hour(14):
            start, end = s.get_window2(c)
            self.assertEqual(end, "17:00")
            self.assertIsInstance(start, str)

    def test_winter_dynamic_end_time_correct(self):
        """Winter dynamic end time must be 15:00 not 17:00."""
        s = DynamicWinterWeekday()
        c = ctx(low_solar=True, soc=20.0, pv_kw=0.5, winter=True)
        with patch_hour(11):
            _, end = s.get_window2(c)
            self.assertEqual(end, "15:00")


    # ── Dynamic window 1 ──────────────────────────────────────────────────────

    def test_window1_before_6_uses_static(self):
        """Before 06:00 window 1 must use static fallback."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=10.0, pv_kw=0.1)
        with patch_hour(5):
            self.assertEqual(s.get_window1(c), ("06:50", "07:00"))

    def test_window1_after_6_uses_dynamic(self):
        """At 06:00+ with SOC below target, window 1 start is calculated."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=10.0, pv_kw=0.16)
        with patch_hour(6):
            start, end = s.get_window1(c)
            self.assertEqual(end, "07:00")
            self.assertNotEqual(start, "06:50")   # not static

    def test_window1_soc_at_target_uses_static(self):
        """SOC already at morning target → static window (no charge needed)."""
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=15.0, pv_kw=0.1)
        with patch_hour(6):
            self.assertEqual(s.get_window1(c), ("06:50", "07:00"))

    def test_window1_real_scenario_soc10_pv016(self):
        """Replay today's log: SOC=10%, PV=0.16kW, target=15% → short window.

        At 5.63kW net rate, 5% of 9.4kWh = ~5 min → start ≥ 06:55.
        Should not enable from 06:50 and overshoot to 18%.
        """
        s = DynamicSummerWeekday()
        c = ctx(low_solar=False, soc=10.0, pv_kw=0.16)
        with patch_hour(6):
            start, end = s.get_window1(c)
            self.assertEqual(end, "07:00")
            h, m = map(int, start.split(":"))
            # Should start after 06:50 — no need for full 10-min static window
            self.assertGreater(h * 60 + m, 6 * 60 + 50)

    def test_window1_low_soc_starts_earlier(self):
        """Very low SOC needs more time → earlier start than high SOC."""
        s   = DynamicSummerWeekday()
        lo  = ctx(low_solar=False, soc=5.0,  pv_kw=0.1)
        hi  = ctx(low_solar=False, soc=13.0, pv_kw=0.1)
        with patch_hour(6):
            start_lo, _ = s.get_window1(lo)
            start_hi, _ = s.get_window1(hi)
            self.assertLessEqual(start_lo, start_hi)

    def test_winter_window1_dynamic_after_6(self):
        """DynamicWinterWeekday window 1 also uses dynamic start after 06:00."""
        s = DynamicWinterWeekday()
        c = ctx(low_solar=False, soc=10.0, pv_kw=0.1, winter=True)
        with patch_hour(6):
            start, end = s.get_window1(c)
            self.assertEqual(end, "07:00")
            self.assertNotEqual(start, "06:30")   # not static

    def test_window1_falls_back_when_soc_none(self):
        """No SOC data → static fallback for window 1."""
        s = DynamicSummerWeekday()
        c = ChargeContext(low_solar=False, soc=None, pv_kw=0.1, winter=False)
        with patch_hour(6):
            self.assertEqual(s.get_window1(c), ("06:50", "07:00"))

    def test_window1_falls_back_when_pv_none(self):
        """No PV data → static fallback for window 1."""
        s = DynamicSummerWeekday()
        c = ChargeContext(low_solar=False, soc=10.0, pv_kw=None, winter=False)
        with patch_hour(6):
            self.assertEqual(s.get_window1(c), ("06:50", "07:00"))

    def test_dynamic_inherits_sunday_evening(self):
        """DynamicSummerWeekend should also get Sunday evening window."""
        s = DynamicSummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertTrue(s.enable1())
            self.assertEqual(s.get_window1(ctx()), ("20:00", "21:00"))
            self.assertEqual(s.morning_target(ctx()), 100)


# ── Sunday evening window ─────────────────────────────────────────────────────
def _next_sunday() -> datetime.date:
    """Return the next Sunday's date."""
    d = datetime.date.today()
    days_ahead = 6 - d.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return d + datetime.timedelta(days=days_ahead)


class TestSundayEveningWindow(unittest.TestCase):

    def test_sunday_evening_enable1_true(self):
        """enable1 should be True on Sunday after 19:00."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertTrue(s.enable1())

    def test_sunday_morning_enable1_false(self):
        """enable1 should be False on Sunday morning."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(10, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertFalse(s.enable1())

    def test_saturday_evening_enable1_false(self):
        """enable1 should be False on Saturday evening — only Sunday."""
        s = SummerWeekend()
        saturday = _next_sunday() - datetime.timedelta(days=1)
        fixed    = datetime.datetime.combine(saturday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertFalse(s.enable1())

    def test_sunday_evening_window1_is_2000_2100(self):
        """Window 1 should be 20:00-21:00 on Sunday evening."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertEqual(s.get_window1(ctx()), ("20:00", "21:00"))

    def test_sunday_morning_window1_is_default(self):
        """Window 1 should be default (disabled slot) on Sunday morning."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(10, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertEqual(s.get_window1(ctx()), ("06:50", "07:00"))

    def test_sunday_evening_morning_target_is_100(self):
        """morning_target should be 100% on Sunday evening."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertEqual(s.morning_target(ctx()), 100)

    def test_sunday_morning_target_is_minimum(self):
        """morning_target should be system minimum on Sunday morning."""
        s = SummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(10, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertEqual(s.morning_target(ctx()), cfg.TARGET_SUMMER_WEEKEND_MORNING)

    def test_dynamic_sunday_inherits_evening_window(self):
        """DynamicSummerWeekend should inherit Sunday evening window 1."""
        s = DynamicSummerWeekend()
        sunday = _next_sunday()
        fixed  = datetime.datetime.combine(sunday, datetime.time(20, 0))
        with patch.object(_strategies_module.datetime, 'datetime', wraps=datetime.datetime) as m:
            m.now.return_value = fixed
            self.assertEqual(s.get_window1(ctx()), ("20:00", "21:00"))
            self.assertTrue(s.enable1())


# ── SOC targets ───────────────────────────────────────────────────────────────
class TestSocTargets(unittest.TestCase):

    def test_summer_weekday_clear(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(ctx(False)), cfg.TARGET_SUMMER_WEEKDAY_MORNING)
        self.assertEqual(s.evening_target(ctx(False)), cfg.TARGET_SUMMER_WEEKDAY_EVENING)

    def test_cloudy_adds_bonus(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(ctx(True)), min(cfg.TARGET_SUMMER_WEEKDAY_MORNING + cfg.CLOUD_BONUS_MORNING, 100))
        self.assertEqual(s.evening_target(ctx(True)), min(cfg.TARGET_SUMMER_WEEKDAY_EVENING + cfg.CLOUD_BONUS_EVENING, 100))

    def test_all_targets_capped_at_100(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            for low in [True, False]:
                self.assertLessEqual(s.morning_target(ctx(low)), 100)
                self.assertLessEqual(s.evening_target(ctx(low)), 100)

    def test_cloudy_target_gte_clear(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertGreaterEqual(s.morning_target(ctx(True)), s.morning_target(ctx(False)))
            self.assertGreaterEqual(s.evening_target(ctx(True)), s.evening_target(ctx(False)))

    def test_manual_targets_always_100(self):
        s = ManualStrategy(is_weekday=True)
        for low in [True, False]:
            self.assertEqual(s.morning_target(ctx(low)), 100)
            self.assertEqual(s.evening_target(ctx(low)), 100)


# ── Manual policy ─────────────────────────────────────────────────────────────
class TestManualStrategyPolicy(unittest.TestCase):

    def test_always(self):
        self.assertTrue(ManualStrategy._resolve("always", True))

    def test_never(self):
        self.assertFalse(ManualStrategy._resolve("never", True))

    def test_weekdays(self):
        self.assertTrue(ManualStrategy._resolve("weekdays", True))
        self.assertFalse(ManualStrategy._resolve("weekdays", False))

    def test_weekends(self):
        self.assertFalse(ManualStrategy._resolve("weekends", True))
        self.assertTrue(ManualStrategy._resolve("weekends", False))

    def test_unknown_defaults_false(self):
        self.assertFalse(ManualStrategy._resolve("bogus", True))


# ── Weather / low_solar ───────────────────────────────────────────────────────
class TestIsLowSolar(unittest.TestCase):

    def test_good_solar_not_low(self):
        self.assertFalse(is_low_solar(SOLAR_GOOD + 1, False))

    def test_poor_solar_is_low(self):
        self.assertTrue(is_low_solar(SOLAR_POOR - 1, False))
        self.assertTrue(is_low_solar(SOLAR_POOR - 1, True))

    def test_marginal_low_summer_not_winter(self):
        marginal = (SOLAR_POOR + SOLAR_GOOD) // 2
        self.assertTrue(is_low_solar(marginal, False))
        self.assertFalse(is_low_solar(marginal, True))

    def test_zero_always_low(self):
        self.assertTrue(is_low_solar(0, False))
        self.assertTrue(is_low_solar(0, True))


# ── Window proximity ──────────────────────────────────────────────────────────
class TestNearWindow(unittest.TestCase):

    def _mock(self, w1s, w1e, w2s, w2e):
        class M:
            def get_window1(self, c): return (w1s, w1e)
            def get_window2(self, c): return (w2s, w2e)
        return M()

    def test_at_window1_start(self):
        self.assertTrue(windows.near_window(dt(10, 0), self._mock("10:00","10:30","15:00","15:40"), ctx()))

    def test_inside_window1(self):
        self.assertTrue(windows.near_window(dt(10, 15), self._mock("10:00","10:30","15:00","15:40"), ctx()))

    def test_lead_time_before_window1(self):
        self.assertTrue(windows.near_window(dt(9, 58), self._mock("10:00","10:30","15:00","15:40"), ctx()))

    def test_too_early(self):
        self.assertFalse(windows.near_window(dt(9, 50), self._mock("10:00","10:30","15:00","15:40"), ctx()))

    def test_after_both_windows(self):
        self.assertFalse(windows.near_window(dt(20, 0), self._mock("06:50","07:00","16:20","17:00"), ctx()))

    def test_near_window2(self):
        self.assertTrue(windows.near_window(dt(16, 20), self._mock("06:50","07:00","16:20","17:00"), ctx()))

    def test_cloudy_earlier_window2_triggers_earlier(self):
        s = SummerWeekday()
        self.assertTrue(windows.near_window(dt(15, 32), s, ctx(True)))
        self.assertFalse(windows.near_window(dt(15, 32), s, ctx(False)))


# ── Window freeze ─────────────────────────────────────────────────────────────
class TestWindowFreeze(unittest.TestCase):

    def test_is_closed_after_end(self):
        self.assertTrue(windows.is_closed(dt(7, 5), "07:00"))

    def test_not_closed_before_end(self):
        self.assertFalse(windows.is_closed(dt(6, 55), "07:00"))

    def test_not_opened_yet_far(self):
        self.assertTrue(windows.is_not_opened_yet(dt(6, 0), "16:20"))

    def test_opened_when_near(self):
        self.assertFalse(windows.is_not_opened_yet(dt(16, 18), "16:20"))


# ── Minutes until ─────────────────────────────────────────────────────────────
class TestMinutesUntil(unittest.TestCase):

    def test_future(self):
        self.assertEqual(windows.minutes_until(dt(10, 0), "10:05"), 5)

    def test_zero(self):
        self.assertEqual(windows.minutes_until(dt(10, 0), "10:00"), 0)

    def test_past(self):
        self.assertLess(windows.minutes_until(dt(10, 10), "10:00"), 0)


# ── Charge state ──────────────────────────────────────────────────────────────
class TestChargeState(unittest.TestCase):
    """Tests for charge_state.py persistence module."""

    def setUp(self):
        """Clear state before each test."""
        import charge_state as cs
        self.cs = cs
        cs.clear()

    def tearDown(self):
        """Always clean up after test."""
        self.cs.clear()

    def test_not_active_when_no_file(self):
        """No state file → not active."""
        self.assertFalse(self.cs.is_active(dt(20, 0)))

    def test_active_before_end_time(self):
        """Saved state with future end → active."""
        self.cs.save("21:00")
        self.assertTrue(self.cs.is_active(dt(20, 30)))

    def test_not_active_after_end_time(self):
        """Saved state with past end → not active, file cleaned up."""
        self.cs.save("20:00")
        self.assertFalse(self.cs.is_active(dt(21, 0)))
        self.assertFalse(self.cs.STATE_FILE.exists())

    def test_exactly_at_end_time_not_active(self):
        """At exact end time → not active."""
        self.cs.save("21:00")
        self.assertFalse(self.cs.is_active(dt(21, 0)))

    def test_clear_removes_file(self):
        """clear() removes state file."""
        self.cs.save("21:00")
        self.assertTrue(self.cs.STATE_FILE.exists())
        self.cs.clear()
        self.assertFalse(self.cs.STATE_FILE.exists())

    def test_clear_when_no_file_is_safe(self):
        """clear() with no file doesn't raise."""
        self.cs.clear()  # no file exists — should not raise
        self.cs.clear()  # second call also safe

    def test_corrupted_file_returns_false(self):
        """Corrupted state file → not active, no crash."""
        self.cs.STATE_FILE.write_text("not valid json {{{")
        self.assertFalse(self.cs.is_active(dt(20, 0)))

    def test_save_overwrites_previous(self):
        """Saving new state overwrites old."""
        self.cs.save("17:00")
        self.cs.save("21:00")
        self.assertTrue(self.cs.is_active(dt(20, 0)))



    def test_mark_and_was_enabled_by_us(self):
        """mark_enabled sets the flag, was_enabled_by_us returns True."""
        self.cs.mark_enabled(1)
        self.assertTrue(self.cs.was_enabled_by_us(1))

    def test_was_enabled_by_us_false_when_not_set(self):
        """was_enabled_by_us returns False when never marked."""
        self.assertFalse(self.cs.was_enabled_by_us(1))
        self.assertFalse(self.cs.was_enabled_by_us(2))

    def test_clear_enabled_removes_marker(self):
        """clear_enabled removes the marker."""
        self.cs.mark_enabled(1)
        self.cs.clear_enabled(1)
        self.assertFalse(self.cs.was_enabled_by_us(1))

    def test_enabled_markers_independent_per_window(self):
        """Window 1 and window 2 markers are independent."""
        self.cs.mark_enabled(1)
        self.assertFalse(self.cs.was_enabled_by_us(2))
        self.cs.mark_enabled(2)
        self.cs.clear_enabled(1)
        self.assertFalse(self.cs.was_enabled_by_us(1))
        self.assertTrue(self.cs.was_enabled_by_us(2))

    def test_clear_enabled_nonexistent_is_safe(self):
        """Clearing a marker that was never set does not raise."""
        self.cs.clear_enabled(2)  # should not raise

    def test_enabled_marker_survives_skip_save(self):
        """Saving skip state does not erase enabled markers."""
        self.cs.mark_enabled(1)
        self.cs.save_skip("07:00")
        self.assertTrue(self.cs.was_enabled_by_us(1))

    def test_enabled_marker_survives_windows_save(self):
        """Saving window config does not erase enabled markers."""
        self.cs.mark_enabled(2)
        self.cs.save_windows("06:56", "07:00", True, "16:20", "17:00", True)
        self.assertTrue(self.cs.was_enabled_by_us(2))

    def test_save_windows_records_last_api_config(self):
        """Last sent windows are stored independently from skip state."""
        self.cs.save_windows("06:50", "07:00", True, "16:12", "17:00", True)
        self.assertEqual(self.cs.get_last_windows()["start2"], "16:12")
        self.assertFalse(self.cs.should_skip(dt(16, 30)))

    def test_expired_skip_keeps_saved_windows(self):
        """Expired target=100 skip should not erase last window config."""
        self.cs.save_windows("06:50", "07:00", True, "16:12", "17:00", True)
        self.cs.save_skip("16:00")
        self.assertFalse(self.cs.should_skip(dt(16, 30)))
        self.assertEqual(self.cs.get_last_windows()["start2"], "16:12")

    def test_clear_skip_keeps_saved_windows(self):
        """Clearing skip state must preserve windows used by proximity checks."""
        self.cs.save_windows("06:50", "07:00", True, "16:12", "17:00", True)
        self.cs.save_skip("17:00")
        self.cs.clear_skip()
        self.assertFalse(self.cs.should_skip(dt(16, 30)))
        self.assertEqual(self.cs.get_last_windows()["start2"], "16:12")


# ── Charge state — Sunday evening scenario ────────────────────────────────────
class TestChargeStateSundayScenario(unittest.TestCase):
    """End-to-end charge state logic for Sunday evening window."""

    def setUp(self):
        import charge_state as cs
        self.cs = cs
        cs.clear()

    def tearDown(self):
        self.cs.clear()

    def test_sunday_window_saved_at_target_100(self):
        """When Sunday evening window enabled with target=100, state is saved."""
        self.cs.save("21:00")
        self.assertTrue(self.cs.is_active(dt(20, 15)))

    def test_sunday_window_expired_at_2100(self):
        """State clears automatically after 21:00."""
        self.cs.save("21:00")
        self.assertFalse(self.cs.is_active(dt(21, 1)))
        self.assertFalse(self.cs.STATE_FILE.exists())

    def test_no_state_saved_at_target_85(self):
        """For target=85%, state should NOT be saved — must keep checking SOC."""
        self.assertFalse(self.cs.is_active(dt(16, 30)))

    def test_full_sunday_timeline(self):
        """Simulate full Sunday evening: enable at 20:00, skip at 20:15, clear at 21:01."""
        self.cs.save("21:00")
        for minute in [2, 15, 30, 45, 58]:
            self.assertTrue(self.cs.is_active(dt(20, minute)),
                            f"Should be active at 20:{minute:02d}")
        self.assertFalse(self.cs.is_active(dt(21, 1)))
        self.assertFalse(self.cs.STATE_FILE.exists())

    def test_not_active_next_morning(self):
        """skip_until saved at Sunday 21:00 must not be active on Monday morning.

        Regression test: old code stored only "HH:MM" so 06:34 < 21:00 kept
        the skip active all next day. Fix stores full ISO datetime.
        """
        import json
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        end_dt = yesterday.replace(hour=21, minute=0, second=0, microsecond=0)
        self.cs.STATE_FILE.write_text(json.dumps({"skip_until": end_dt.isoformat()}))
        self.assertFalse(self.cs.is_active(dt(6, 34)))
        self.assertFalse(self.cs.STATE_FILE.exists())

    def test_state_survives_multiple_reads(self):
        """Reading state multiple times doesn't corrupt it."""
        self.cs.save("21:00")
        for _ in range(5):
            self.assertTrue(self.cs.is_active(dt(20, 30)))
        self.assertTrue(self.cs.STATE_FILE.exists())


class TestSchedulerChargeStateUpdate(unittest.TestCase):
    """Regression coverage for skip_until updates from the scheduler plan."""

    def setUp(self):
        import charge_state as cs
        self.cs = cs
        cs.clear()

    def tearDown(self):
        self.cs.clear()

    def _plan(self, w2_enabled=True):
        return ChargePlan(
            window1=ChargeWindow("06:45", "07:00", True),
            window2=ChargeWindow("15:30", "17:00", w2_enabled),
            morning_target=25,
            evening_target=100,
        )

    def test_future_enabled_100_percent_window_does_not_skip_all_day(self):
        """An enabled afternoon API window must not block morning checks."""
        _scheduler_module._update_charge_state(dt(6, 42), self._plan())
        self.assertFalse(self.cs.should_skip(dt(6, 44)))

    def test_near_enabled_100_percent_window_sets_skip(self):
        """Once the 100% window is near, skip_until can suppress API churn."""
        _scheduler_module._update_charge_state(dt(15, 28), self._plan())
        self.assertTrue(self.cs.should_skip(dt(15, 29)))


# ── Window in progress ────────────────────────────────────────────────────────
class TestWindowInProgress(unittest.TestCase):
    """Tests for window_in_progress — used to lock API times during charging."""

    def test_inside_window(self):
        self.assertTrue(window_in_progress(dt(16, 30), "16:11", "17:00"))

    def test_at_start(self):
        self.assertTrue(window_in_progress(dt(16, 11), "16:11", "17:00"))

    def test_before_start(self):
        self.assertFalse(window_in_progress(dt(16, 10), "16:11", "17:00"))

    def test_at_end_not_active(self):
        self.assertFalse(window_in_progress(dt(17, 0), "16:11", "17:00"))

    def test_after_end(self):
        self.assertFalse(window_in_progress(dt(17, 30), "16:11", "17:00"))

    def test_morning_window(self):
        self.assertTrue(window_in_progress(dt(6, 55), "06:50", "07:00"))

    def test_before_morning_window(self):
        self.assertFalse(window_in_progress(dt(6, 49), "06:50", "07:00"))

    def test_sunday_evening_window(self):
        self.assertTrue(window_in_progress(dt(20, 30), "20:00", "21:00"))

    def test_dynamic_scenario_16_11_to_17(self):
        """Replay real scenario: window set to 16:11-17:00 at 16:16.
        Should be in progress — API times must be kept."""
        self.assertTrue(window_in_progress(dt(16, 16), "16:11", "17:00"))
        self.assertTrue(window_in_progress(dt(16, 30), "16:11", "17:00"))
        self.assertTrue(window_in_progress(dt(16, 59), "16:11", "17:00"))
        self.assertFalse(window_in_progress(dt(17, 0),  "16:11", "17:00"))


class TestSavedWindowRelevant(unittest.TestCase):
    """Saved enabled windows wake the scheduler before/during API window."""

    def test_near_saved_window_start(self):
        saved = {
            "start1": "06:50", "end1": "07:00", "enabled1": True,
            "start2": "16:12", "end2": "17:00", "enabled2": True,
        }
        self.assertTrue(saved_window_relevant(dt(16, 10), saved, 2))

    def test_saved_window_in_progress(self):
        saved = {
            "start1": "06:50", "end1": "07:00", "enabled1": True,
            "start2": "16:12", "end2": "17:00", "enabled2": True,
        }
        self.assertTrue(saved_window_relevant(dt(16, 16), saved, 2))

    def test_before_saved_window_lead(self):
        saved = {
            "start1": "06:50", "end1": "07:00", "enabled1": True,
            "start2": "16:12", "end2": "17:00", "enabled2": True,
        }
        # 4 minutes before window — assumes WINDOW_LEAD_MINUTES < 4
        self.assertFalse(saved_window_relevant(dt(16, 8), saved, 2))

    def test_disabled_saved_window_not_relevant(self):
        saved = {
            "start1": "06:50", "end1": "07:00", "enabled1": True,
            "start2": "16:12", "end2": "17:00", "enabled2": False,
        }
        self.assertFalse(saved_window_relevant(dt(16, 16), saved, 2))


# ── proximity_check ───────────────────────────────────────────────────────────
class TestProximityCheck(unittest.TestCase):
    """Tests for proximity_check — the three-phase scheduler gate."""

    LAT, LON = 50.0, 18.0

    def _strategy(self, w1s="06:50", w1e="07:00", w2s="16:20", w2e="17:00"):
        """Minimal strategy stub returning fixed window times."""
        class S:
            def get_window1(self, c): return (w1s, w1e)
            def get_window2(self, c): return (w2s, w2e)
        return S()

    # ── Phase 1: skip before solar fetch ─────────────────────────────────────

    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_skips_when_not_near_any_window(self, _mock_state, _mock_near):
        """Far from all windows and no saved state → skip without fetching solar."""
        result = proximity_check(dt(10, 0), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertFalse(result.should_run)
        self.assertIn("[SKIP]", result.skip_reason)

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_skip_before_solar_does_not_call_weather(
            self, _mock_state, _mock_near, _mock_low, mock_forecast):
        """Phase 1 skip must not fetch solar forecast — no API call."""
        proximity_check(dt(10, 0), self._strategy(), force=False,
                        winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        mock_forecast.assert_not_called()

    # ── Phase 2: skip after solar fetch ──────────────────────────────────────

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", side_effect=[True, False])
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_skips_after_solar_on_clear_day_not_near_window(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """Clear day + not near clear-day window → skip after solar fetch."""
        result = proximity_check(dt(10, 0), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertFalse(result.should_run)
        self.assertIn("solar check", result.skip_reason)
        self.assertFalse(result.low_solar)

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", side_effect=[True, False])
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_skip_after_solar_carries_radiation(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """ProximityResult after solar skip should carry radiation value."""
        result = proximity_check(dt(10, 0), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertEqual(result.radiation, 500)

    # ── Should run ────────────────────────────────────────────────────────────

    @patch("proximity.weather.get_solar_forecast", return_value=100)
    @patch("proximity.weather.is_low_solar", return_value=True)
    @patch("proximity.windows.near_window", return_value=True)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_runs_when_near_window_and_low_solar(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """Near a window on a cloudy day → should run."""
        result = proximity_check(dt(16, 18), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertTrue(result.should_run)

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", return_value=True)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_runs_when_near_window_on_clear_day(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """Near a window even on a clear day → should run."""
        result = proximity_check(dt(16, 18), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertTrue(result.should_run)

    # ── force=True bypasses all proximity logic ───────────────────────────────

    @patch("proximity.weather.get_solar_forecast", return_value=100)
    @patch("proximity.weather.is_low_solar", return_value=True)
    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_force_bypasses_proximity(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """force=True must always run regardless of window proximity."""
        result = proximity_check(dt(10, 0), self._strategy(), force=True,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertTrue(result.should_run)

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_force_bypasses_solar_skip(
            self, _mock_state, _mock_near, _mock_low, _mock_forecast):
        """force=True on a clear day far from windows must still run."""
        result = proximity_check(dt(10, 0), self._strategy(), force=True,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertTrue(result.should_run)

    # ── Phase 0: saved window wakes the scheduler ─────────────────────────────

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.windows.minutes_until", return_value=2)
    @patch("proximity.charge_state.get_last_windows")
    def test_saved_window_near_forces_run(
            self, mock_state, _mock_mins, _mock_near, _mock_low, _mock_forecast):
        """Saved enabled window within lead time → run even if not near current strategy window."""
        mock_state.return_value = {
            "start1": "06:50", "end1": "07:00", "enabled1": False,
            "start2": "16:12", "end2": "17:00", "enabled2": True,
        }
        result = proximity_check(dt(16, 10), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertTrue(result.should_run)

    @patch("proximity.weather.get_solar_forecast", return_value=500)
    @patch("proximity.weather.is_low_solar", return_value=False)
    @patch("proximity.windows.near_window", return_value=False)
    @patch("proximity.charge_state.get_last_windows")
    def test_no_saved_windows_does_not_force_run(
            self, mock_state, _mock_near, _mock_low, _mock_forecast):
        """No saved state → phase 0 does not prevent skipping."""
        mock_state.return_value = None
        result = proximity_check(dt(10, 0), self._strategy(), force=False,
                                 winter=False, forecast_lat=self.LAT, forecast_lon=self.LON)
        self.assertFalse(result.should_run)

    # ── Forecast coords are passed through ────────────────────────────────────

    @patch("proximity.weather.get_solar_forecast", return_value=100)
    @patch("proximity.weather.is_low_solar", return_value=True)
    @patch("proximity.windows.near_window", return_value=True)
    @patch("proximity.charge_state.get_last_windows", return_value=None)
    def test_forecast_coords_forwarded(
            self, _mock_state, _mock_near, _mock_low, mock_forecast):
        """Lat/lon passed to proximity_check must reach get_solar_forecast."""
        proximity_check(dt(16, 18), self._strategy(), force=False,
                        winter=False, forecast_lat=51.5, forecast_lon=19.3)
        mock_forecast.assert_called_once_with(51.5, 19.3)




# ── Savings tracker ───────────────────────────────────────────────────────────
import os, tempfile, pathlib
os.environ.setdefault("FOXESS_BATTERY_CHARGE_RATE_KW", "5.63")
os.environ.setdefault("FOXESS_BATTERY_KWH", "9.4")
os.environ.setdefault("FOXESS_TARIFF", "g13s_dynamic")
import savings as _savings_module


class TestSavingsRates(unittest.TestCase):
    """Tests for _rates() — correct price pair per season/day/slot."""

    def _r(self, winter, weekend, start):
        return _savings_module._rates(winter, weekend, start)

    def test_summer_weekday_window1(self):
        """Window 1 morning: charges at summer cheap, avoids summer peak."""
        charge, peak = self._r(winter=False, weekend=False, start="06:50")
        self.assertEqual(charge, _savings_module.PRICE_SUMMER_WD_CHEAP)
        self.assertEqual(peak,   _savings_module.PRICE_SUMMER_WD_PEAK)

    def test_summer_weekday_window2(self):
        """Window 2 afternoon: charges at summer cheap, avoids summer peak."""
        charge, peak = self._r(winter=False, weekend=False, start="15:45")
        self.assertEqual(charge, _savings_module.PRICE_SUMMER_WD_CHEAP)
        self.assertEqual(peak,   _savings_module.PRICE_SUMMER_WD_PEAK)

    def test_winter_weekday_window1(self):
        """Winter weekday morning: charges at midday rate, avoids winter peak."""
        charge, peak = self._r(winter=True, weekend=False, start="06:30")
        self.assertEqual(charge, _savings_module.PRICE_WINTER_WD_MIDDAY)
        self.assertEqual(peak,   _savings_module.PRICE_WINTER_WD_PEAK)

    def test_winter_weekday_window2(self):
        """Winter weekday midday: charges at midday rate, avoids winter peak."""
        charge, peak = self._r(winter=True, weekend=False, start="13:00")
        self.assertEqual(charge, _savings_module.PRICE_WINTER_WD_MIDDAY)
        self.assertEqual(peak,   _savings_module.PRICE_WINTER_WD_PEAK)

    def test_summer_weekend_window2(self):
        """Summer weekend: charges at very cheap rate, avoids neutral."""
        charge, peak = self._r(winter=False, weekend=True, start="16:20")
        self.assertEqual(charge, _savings_module.PRICE_SUMMER_WE_CHEAPEST)
        self.assertEqual(peak,   _savings_module.PRICE_SUMMER_WE_NEUTRAL)

    def test_winter_weekend_window2(self):
        """Winter weekend: charges at cheapest midday, avoids neutral."""
        charge, peak = self._r(winter=True, weekend=True, start="14:20")
        self.assertEqual(charge, _savings_module.PRICE_WINTER_WE_CHEAPEST)
        self.assertEqual(peak,   _savings_module.PRICE_WINTER_WE_NEUTRAL)

    def test_sunday_evening_summer(self):
        """Sunday evening (20:00) summer: night rate, avoids Monday peak."""
        charge, peak = self._r(winter=False, weekend=True, start="20:00")
        self.assertEqual(charge, _savings_module.PRICE_SUMMER_WD_NIGHT)
        self.assertEqual(peak,   _savings_module.PRICE_SUMMER_WD_PEAK)

    def test_sunday_evening_winter(self):
        """Sunday evening (20:00) winter: night rate, avoids Monday peak."""
        charge, peak = self._r(winter=True, weekend=True, start="20:00")
        self.assertEqual(charge, _savings_module.PRICE_WINTER_WD_NIGHT)
        self.assertEqual(peak,   _savings_module.PRICE_WINTER_WD_PEAK)

    def test_charge_always_less_than_peak(self):
        """Charge rate must always be lower than peak rate — otherwise no saving."""
        scenarios = [
            (False, False, "06:50"),
            (False, False, "15:30"),
            (True,  False, "06:30"),
            (True,  False, "13:00"),
            (False, True,  "16:20"),
            (True,  True,  "14:20"),
            (False, True,  "20:00"),
            (True,  True,  "20:00"),
        ]
        for winter, weekend, start in scenarios:
            charge, peak = self._r(winter, weekend, start)
            self.assertLess(charge, peak,
                msg=f"charge {charge} >= peak {peak} for winter={winter} weekend={weekend} start={start}")


class TestSavingsRecord(unittest.TestCase):
    """Tests for record_session() and query_savings() with isolated DB."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._orig_db = _savings_module.DB_FILE
        _savings_module.DB_FILE = pathlib.Path(self._tmp.name)

    def tearDown(self):
        _savings_module.DB_FILE = self._orig_db
        pathlib.Path(self._tmp.name).unlink(missing_ok=True)

    def _record(self, start="06:56", end="07:00", soc=10.0,
                winter=False, weekend=False, window=1):
        _savings_module.record_session(
            window, start, end, soc, winter,
            "G13s DYNAMIC SUMMER weekday", weekend
        )

    def test_record_and_retrieve(self):
        """Recorded session appears in query_savings('all')."""
        self._record()
        sessions = _savings_module.query_savings("all")
        self.assertEqual(len(sessions), 1)
        s = sessions[0]
        self.assertEqual(s.window, 1)
        self.assertEqual(s.start_time, "06:56")
        self.assertEqual(s.end_time, "07:00")
        self.assertEqual(s.duration_min, 4)
        self.assertAlmostEqual(s.kwh, 4/60 * 5.63, places=2)

    def test_duration_zero_not_recorded(self):
        """Session with end == start is silently dropped."""
        self._record(start="07:00", end="07:00")
        self.assertEqual(len(_savings_module.query_savings("all")), 0)

    def test_duration_negative_not_recorded(self):
        """Session with end < start is silently dropped."""
        self._record(start="07:05", end="07:00")
        self.assertEqual(len(_savings_module.query_savings("all")), 0)

    def test_soc_none_recorded(self):
        """soc_start=None is stored and retrieved as None."""
        _savings_module.record_session(
            1, "06:56", "07:00", None, False, "test", False
        )
        s = _savings_module.query_savings("all")[0]
        self.assertIsNone(s.soc_start)

    def test_saved_pln_positive(self):
        """Saving must be positive (cheap rate < peak rate)."""
        self._record()
        s = _savings_module.query_savings("all")[0]
        self.assertGreater(s.saved_pln, 0)

    def test_kwh_calculation(self):
        """60 min at 5.63 kW = 5.63 kWh."""
        self._record(start="16:00", end="17:00", window=2)
        s = _savings_module.query_savings("all")[0]
        self.assertAlmostEqual(s.kwh, 5.63, places=2)

    def test_multiple_sessions_all(self):
        """Multiple sessions all returned by 'all'."""
        self._record(start="06:56", end="07:00", window=1)
        self._record(start="15:45", end="17:00", window=2)
        self.assertEqual(len(_savings_module.query_savings("all")), 2)

    def test_query_by_month(self):
        """YYYY-MM filter returns only matching month."""
        self._record()
        now = datetime.datetime.now()
        month = now.strftime("%Y-%m")
        wrong_month = (now.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
        self.assertEqual(len(_savings_module.query_savings(month)), 1)
        self.assertEqual(len(_savings_module.query_savings(wrong_month)), 0)

    def test_query_7d(self):
        """'7d' returns today's session."""
        self._record()
        self.assertEqual(len(_savings_module.query_savings("7d")), 1)

    def test_query_unknown_period(self):
        """Unknown period string returns empty list, no crash."""
        self._record()
        result = _savings_module.query_savings("bogus")
        self.assertEqual(result, [])


class TestSavingsSummary(unittest.TestCase):
    """Tests for savings_summary() aggregation."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._orig_db = _savings_module.DB_FILE
        _savings_module.DB_FILE = pathlib.Path(self._tmp.name)

    def tearDown(self):
        _savings_module.DB_FILE = self._orig_db
        pathlib.Path(self._tmp.name).unlink(missing_ok=True)

    def test_empty_summary(self):
        """No sessions → summary with zeros."""
        s = _savings_module.savings_summary("all")
        self.assertEqual(s["sessions"], 0)
        self.assertEqual(s["kwh"], 0.0)
        self.assertEqual(s["saved_pln"], 0.0)

    def test_summary_aggregates_correctly(self):
        """kwh and saved_pln are sums across all sessions."""
        _savings_module.record_session(1, "06:56", "07:00", 10.0, False, "test", False)
        _savings_module.record_session(2, "15:45", "17:00", 28.0, False, "test", False)
        s = _savings_module.savings_summary("all")
        self.assertEqual(s["sessions"], 2)
        sessions = s["sessions_detail"]
        self.assertAlmostEqual(s["kwh"],      sum(x.kwh for x in sessions),      places=2)
        self.assertAlmostEqual(s["saved_pln"], sum(x.saved_pln for x in sessions), places=2)

    def test_summary_contains_sessions_detail(self):
        """Non-empty summary includes sessions_detail list."""
        _savings_module.record_session(1, "06:56", "07:00", 10.0, False, "test", False)
        s = _savings_module.savings_summary("all")
        self.assertIn("sessions_detail", s)
        self.assertEqual(len(s["sessions_detail"]), 1)

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
