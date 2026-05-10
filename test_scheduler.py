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
from context import ChargeContext
from weather import is_low_solar, SOLAR_GOOD, SOLAR_POOR
import windows
import config as cfg
from unittest.mock import patch
import strategies as _strategies_module


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
        self.assertFalse(SummerWeekend().enable1())

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


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
