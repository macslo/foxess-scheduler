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
    ManualStrategy,
)
from weather import is_low_solar, SOLAR_GOOD, SOLAR_POOR
from foxess_grid_charge_scheduler import _minutes_until, _near_window, _is_closed, _is_not_opened_yet
import config as cfg


def dt(h, m=0):
    return datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)

def date(year, month, day):
    return datetime.date(year, month, day)


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

    def test_winter_boundary_october(self):
        s = get_strategy(date(2025, 10, 1), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_winter_boundary_march(self):
        s = get_strategy(date(2025, 3, 31), "g13s")
        self.assertIsInstance(s, (WinterWeekday, WinterWeekend))

    def test_summer_boundary_april(self):
        s = get_strategy(date(2025, 4, 1), "g13s")
        self.assertIsInstance(s, (SummerWeekday, SummerWeekend))

    def test_summer_boundary_september(self):
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
        self.assertFalse(SummerWeekend().enable1())

    def test_summer_weekend_window2_follows_config(self):
        self.assertEqual(SummerWeekend().enable2(), cfg.G13S_WEEKEND_MIDDAY)

    def test_winter_weekday_both_enabled(self):
        s = WinterWeekday()
        self.assertTrue(s.enable1())
        self.assertTrue(s.enable2())

    def test_winter_weekend_window1_disabled(self):
        self.assertFalse(WinterWeekend().enable1())

    def test_winter_weekend_window2_follows_config(self):
        self.assertEqual(WinterWeekend().enable2(), cfg.G13S_WEEKEND_MIDDAY)


# ── Dynamic window times ──────────────────────────────────────────────────────
class TestStrategyWindowTimes(unittest.TestCase):

    def test_summer_weekday_window1_clear(self):
        self.assertEqual(SummerWeekday().get_window1(False), ("06:50", "07:00"))

    def test_summer_weekday_window1_cloudy(self):
        self.assertEqual(SummerWeekday().get_window1(True), ("06:45", "07:00"))

    def test_summer_weekday_window2_clear(self):
        self.assertEqual(SummerWeekday().get_window2(False), ("16:20", "17:00"))

    def test_summer_weekday_window2_cloudy(self):
        self.assertEqual(SummerWeekday().get_window2(True), ("15:30", "17:00"))

    def test_winter_weekday_window1_same_both(self):
        s = WinterWeekday()
        self.assertEqual(s.get_window1(False), s.get_window1(True))

    def test_winter_weekday_window2_clear(self):
        self.assertEqual(WinterWeekday().get_window2(False), ("14:20", "15:00"))

    def test_winter_weekday_window2_cloudy(self):
        self.assertEqual(WinterWeekday().get_window2(True), ("13:00", "15:00"))

    def test_cloudy_window1_starts_same_or_earlier(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertLessEqual(s.get_window1(True)[0], s.get_window1(False)[0])

    def test_cloudy_window2_starts_earlier(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertLessEqual(s.get_window2(True)[0], s.get_window2(False)[0])


# ── SOC targets ───────────────────────────────────────────────────────────────
class TestSocTargets(unittest.TestCase):

    def test_summer_weekday_clear(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(False), cfg.TARGET_SUMMER_WEEKDAY_MORNING)
        self.assertEqual(s.evening_target(False), cfg.TARGET_SUMMER_WEEKDAY_EVENING)

    def test_cloudy_adds_bonus(self):
        s = SummerWeekday()
        self.assertEqual(s.morning_target(True), min(cfg.TARGET_SUMMER_WEEKDAY_MORNING + cfg.CLOUD_BONUS_MORNING, 100))
        self.assertEqual(s.evening_target(True), min(cfg.TARGET_SUMMER_WEEKDAY_EVENING + cfg.CLOUD_BONUS_EVENING, 100))

    def test_all_targets_capped_at_100(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            for low in [True, False]:
                self.assertGreater(s.morning_target(low), 0)
                self.assertGreater(s.evening_target(low), 0)
                self.assertLessEqual(s.morning_target(low), 100)
                self.assertLessEqual(s.evening_target(low), 100)

    def test_cloudy_target_gte_clear(self):
        for S in [SummerWeekday, SummerWeekend, WinterWeekday, WinterWeekend]:
            s = S()
            self.assertGreaterEqual(s.morning_target(True), s.morning_target(False))
            self.assertGreaterEqual(s.evening_target(True), s.evening_target(False))

    def test_manual_targets_always_100(self):
        s = ManualStrategy(is_weekday=True)
        for low in [True, False]:
            self.assertEqual(s.morning_target(low), 100)
            self.assertEqual(s.evening_target(low), 100)


# ── Manual policy resolution ──────────────────────────────────────────────────
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

    def test_unknown_defaults_false(self):
        self.assertFalse(ManualStrategy._resolve("bogus", True))


# ── Weather / low_solar ───────────────────────────────────────────────────────
class TestIsLowSolar(unittest.TestCase):

    def test_good_solar_not_low(self):
        self.assertFalse(is_low_solar(SOLAR_GOOD + 1, False))
        self.assertFalse(is_low_solar(SOLAR_GOOD + 1, True))

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
            window1           = (w1s, w1e)
            window1_low_solar = (w1s, w1e)
            window2           = (w2s, w2e)
            window2_low_solar = (w2s, w2e)
            def get_window1(self, ls): return self.window1
            def get_window2(self, ls): return self.window2
        return M()

    def test_at_window1_start(self):
        self.assertTrue(_near_window(dt(10, 0), self._mock("10:00","10:30","15:00","15:40"), False))

    def test_inside_window1(self):
        self.assertTrue(_near_window(dt(10, 15), self._mock("10:00","10:30","15:00","15:40"), False))

    def test_lead_time_before_window1(self):
        self.assertTrue(_near_window(dt(9, 58), self._mock("10:00","10:30","15:00","15:40"), False))

    def test_too_early(self):
        self.assertFalse(_near_window(dt(9, 50), self._mock("10:00","10:30","15:00","15:40"), False))

    def test_after_both_windows(self):
        self.assertFalse(_near_window(dt(20, 0), self._mock("06:50","07:00","16:20","17:00"), False))

    def test_near_window2(self):
        self.assertTrue(_near_window(dt(16, 20), self._mock("06:50","07:00","16:20","17:00"), False))

    def test_cloudy_earlier_window2_triggers_earlier(self):
        s = SummerWeekday()
        self.assertTrue(_near_window(dt(15, 46), s, True))
        self.assertFalse(_near_window(dt(15, 46), s, False))


# ── Window freeze ─────────────────────────────────────────────────────────────
class TestWindowFreeze(unittest.TestCase):

    def test_is_closed_after_end(self):
        self.assertTrue(_is_closed(dt(7, 5), "07:00"))

    def test_not_closed_before_end(self):
        self.assertFalse(_is_closed(dt(6, 55), "07:00"))

    def test_not_opened_yet_far(self):
        self.assertTrue(_is_not_opened_yet(dt(6, 0), "16:20"))

    def test_not_not_opened_near(self):
        self.assertFalse(_is_not_opened_yet(dt(16, 18), "16:20"))


# ── Minutes until ─────────────────────────────────────────────────────────────
class TestMinutesUntil(unittest.TestCase):

    def test_future(self):
        self.assertEqual(_minutes_until(dt(10, 0), "10:05"), 5)

    def test_zero(self):
        self.assertEqual(_minutes_until(dt(10, 0), "10:00"), 0)

    def test_past(self):
        self.assertLess(_minutes_until(dt(10, 10), "10:00"), 0)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
