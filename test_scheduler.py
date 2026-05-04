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

# ── Ensure local modules are importable ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))

from strategies import (
    get_strategy,
    SummerWeekday, SummerWeekend,
    WinterWeekday, WinterWeekend,
    ManualStrategy,
)
from weather import is_low_solar, SOLAR_GOOD, SOLAR_POOR
from foxess_grid_charge_scheduler import _minutes_until, _near_window
import config as cfg


# ── Helpers ───────────────────────────────────────────────────────────────────
def dt(h, m=0):
    """Create a datetime for today at h:m."""
    return datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)


def date(year, month, day):
    return datetime.date(year, month, day)


# ── Strategy selection ────────────────────────────────────────────────────────
class TestGetStrategy(unittest.TestCase):

    def test_summer_weekday(self):
        # Monday 5 May 2025
        s = get_strategy(date(2025, 5, 5), "g13s")
        self.assertIsInstance(s, SummerWeekday)

    def test_summer_weekend(self):
        # Saturday 10 May 2025
        s = get_strategy(date(2025, 5, 10), "g13s")
        self.assertIsInstance(s, SummerWeekend)

    def test_winter_weekday(self):
        # Monday 3 Nov 2025
        s = get_strategy(date(2025, 11, 3), "g13s")
        self.assertIsInstance(s, WinterWeekday)

    def test_winter_weekend(self):
        # Sunday 2 Nov 2025
        s = get_strategy(date(2025, 11, 2), "g13s")
        self.assertIsInstance(s, WinterWeekend)

    def test_winter_boundary_october(self):
        # 1 Oct is winter
        s = get_strategy(date(2025, 10, 1), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_winter_boundary_march(self):
        # 31 Mar is still winter
        s = get_strategy(date(2025, 3, 31), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_summer_boundary_april(self):
        # 1 Apr is summer
        s = get_strategy(date(2025, 4, 1), "g13s")
        self.assertIsInstance(s, (SummerWeekday, SummerWeekend))

    def test_summer_boundary_september(self):
        # 30 Sep is still summer
        s = get_strategy(date(2025, 9, 30), "g13s")
        self.assertIsInstance(s, (SummerWeekday, SummerWeekend))

    def test_manual_weekday(self):
        s = get_strategy(date(2025, 5, 5), "manual")
        self.assertIsInstance(s, ManualStrategy)
        self.assertIn("weekday", s.name)

    def test_manual_weekend(self):
        s = get_strategy(date(2025, 5, 10), "manual")
        self.assertIsInstance(s, ManualStrategy)
        self.assertIn("weekend", s.name)


# ── Strategy enable logic ─────────────────────────────────────────────────────
class TestStrategyEnableLogic(unittest.TestCase):

    def test_summer_weekday_both_enabled(self):
        s = SummerWeekday()
        self.assertTrue(s.enable1())
        self.assertTrue(s.enable2())

    def test_summer_weekend_window1_disabled(self):
        s = SummerWeekend()
        self.assertFalse(s.enable1())   # no morning peak on weekends

    def test_summer_weekend_window2_follows_config(self):
        s = SummerWeekend()
        self.assertEqual(s.enable2(), cfg.G13S_WEEKEND_MIDDAY)

    def test_winter_weekday_both_enabled(self):
        s = WinterWeekday()
        self.assertTrue(s.enable1())
        self.assertTrue(s.enable2())

    def test_winter_weekend_window1_disabled(self):
        s = WinterWeekend()
        self.assertFalse(s.enable1())   # no morning peak on weekends

    def test_winter_weekend_window2_follows_config(self):
        s = WinterWeekend()
        self.assertEqual(s.enable2(), cfg.G13S_WEEKEND_MIDDAY)


# ── Strategy window times ─────────────────────────────────────────────────────
class TestStrategyWindowTimes(unittest.TestCase):

    def test_summer_weekday_windows(self):
        s = SummerWeekday()
        self.assertEqual(s.window1, ("06:50", "07:00"))
        self.assertEqual(s.window2, ("16:20", "17:00"))

    def test_summer_weekend_windows(self):
        s = SummerWeekend()
        self.assertEqual(s.window1, ("06:50", "07:00"))
        self.assertEqual(s.window2, ("16:20", "17:00"))

    def test_winter_weekday_windows(self):
        s = WinterWeekday()
        self.assertEqual(s.window1, ("06:30", "07:00"))
        self.assertEqual(s.window2, ("14:20", "15:00"))

    def test_winter_weekend_windows(self):
        s = WinterWeekend()
        self.assertEqual(s.window1, ("06:50", "07:00"))
        self.assertEqual(s.window2, ("14:20", "15:00"))


# ── SOC targets ───────────────────────────────────────────────────────────────
class TestSocTargets(unittest.TestCase):

    def test_summer_weekday_clear_day(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(False), cfg.TARGET_SUMMER_WEEKDAY_MORNING)
        self.assertEqual(s.evening_target(False), cfg.TARGET_SUMMER_WEEKDAY_EVENING)

    def test_summer_weekday_cloudy_adds_bonus(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(True), min(cfg.TARGET_SUMMER_WEEKDAY_MORNING + cfg.CLOUD_BONUS_MORNING, 95))
        self.assertEqual(s.evening_target(True), min(cfg.TARGET_SUMMER_WEEKDAY_EVENING + cfg.CLOUD_BONUS_EVENING, 95))

    def test_winter_weekday_evening_capped_at_95(self):
        s = WinterWeekday()
        # 95 + any bonus should still be capped at 95
        self.assertLessEqual(s.evening_target(True), 95)
        self.assertLessEqual(s.evening_target(False), 95)

    def test_all_targets_capped_at_95(self):
        for StratClass in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = StratClass()
            for low_solar in [True, False]:
                self.assertLessEqual(s.morning_target(low_solar), 95)
                self.assertLessEqual(s.evening_target(low_solar), 95)

    def test_cloudy_target_always_gte_clear(self):
        for StratClass in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = StratClass()
            self.assertGreaterEqual(s.morning_target(True), s.morning_target(False))
            self.assertGreaterEqual(s.evening_target(True), s.evening_target(False))

    def test_manual_strategy_targets_always_100(self):
        s = ManualStrategy(is_weekday=True)
        self.assertEqual(s.morning_target(True), 100)
        self.assertEqual(s.morning_target(False), 100)
        self.assertEqual(s.evening_target(True), 100)
        self.assertEqual(s.evening_target(False), 100)


# ── Manual strategy policy resolution ────────────────────────────────────────
class TestManualStrategyPolicy(unittest.TestCase):

    def test_always(self):
        self.assertTrue(ManualStrategy._resolve("always", True))
        self.assertTrue(ManualStrategy._resolve("always", False))

    def test_never(self):
        self.assertFalse(ManualStrategy._resolve("never", True))
        self.assertFalse(ManualStrategy._resolve("never", False))

    def test_weekdays(self):
        self.assertTrue(ManualStrategy._resolve("weekdays", True))
        self.assertFalse(ManualStrategy._resolve("weekdays", False))

    def test_weekends(self):
        self.assertFalse(ManualStrategy._resolve("weekends", True))
        self.assertTrue(ManualStrategy._resolve("weekends", False))

    def test_unknown_policy_defaults_to_false(self):
        self.assertFalse(ManualStrategy._resolve("bogus", True))


# ── Weather / low_solar logic ─────────────────────────────────────────────────
class TestIsLowSolar(unittest.TestCase):

    def test_good_solar_not_low(self):
        self.assertFalse(is_low_solar(SOLAR_GOOD + 1, winter=False))
        self.assertFalse(is_low_solar(SOLAR_GOOD + 1, winter=True))

    def test_poor_solar_is_low(self):
        self.assertTrue(is_low_solar(SOLAR_POOR - 1, winter=False))
        self.assertTrue(is_low_solar(SOLAR_POOR - 1, winter=True))

    def test_marginal_solar_low_in_summer_not_winter(self):
        # Between SOLAR_POOR and SOLAR_GOOD = marginal
        marginal = (SOLAR_POOR + SOLAR_GOOD) // 2
        self.assertTrue(is_low_solar(marginal, winter=False))   # summer: marginal = low
        self.assertFalse(is_low_solar(marginal, winter=True))   # winter: only truly poor triggers

    def test_zero_radiation_always_low(self):
        self.assertTrue(is_low_solar(0, winter=False))
        self.assertTrue(is_low_solar(0, winter=True))


# ── Window proximity (_near_window) ──────────────────────────────────────────
class TestNearWindow(unittest.TestCase):

    def _strategy_with_windows(self, w1_start, w1_end, w2_start, w2_end):
        """Create a minimal mock strategy with given window times."""
        class MockStrategy:
            window1 = (w1_start, w1_end)
            window2 = (w2_start, w2_end)
        return MockStrategy()

    def test_exactly_at_window1_start(self):
        s = self._strategy_with_windows("10:00", "10:30", "15:00", "15:40")
        self.assertTrue(_near_window(dt(10, 0), s))

    def test_inside_window1(self):
        s = self._strategy_with_windows("10:00", "10:30", "15:00", "15:40")
        self.assertTrue(_near_window(dt(10, 15), s))

    def test_lead_time_before_window1(self):
        s = self._strategy_with_windows("10:00", "10:30", "15:00", "15:40")
        # 2 min before start, lead=3 → should trigger
        self.assertTrue(_near_window(dt(9, 58), s))

    def test_too_early_for_window1(self):
        s = self._strategy_with_windows("10:00", "10:30", "15:00", "15:40")
        # 10 min before start, lead=3 → should not trigger
        self.assertFalse(_near_window(dt(9, 50), s))

    def test_after_both_windows(self):
        s = self._strategy_with_windows("06:50", "07:00", "16:20", "17:00")
        self.assertFalse(_near_window(dt(20, 0), s))

    def test_near_window2(self):
        s = self._strategy_with_windows("06:50", "07:00", "16:20", "17:00")
        self.assertTrue(_near_window(dt(16, 20), s))

    def test_inside_window2(self):
        s = self._strategy_with_windows("06:50", "07:00", "16:20", "17:00")
        self.assertTrue(_near_window(dt(16, 40), s))


# ── Minutes until ─────────────────────────────────────────────────────────────
class TestMinutesUntil(unittest.TestCase):

    def test_positive_future(self):
        now = dt(10, 0)
        self.assertEqual(_minutes_until(now, "10:05"), 5)

    def test_zero_at_start(self):
        now = dt(10, 0)
        self.assertEqual(_minutes_until(now, "10:00"), 0)

    def test_negative_past(self):
        now = dt(10, 10)
        self.assertLess(_minutes_until(now, "10:00"), 0)


# ── Run tests ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
