"""Proximity checks for deciding whether the scheduler should run."""

import datetime

import charge_state
import config as cfg
import weather
import windows
from scheduler_models import ChargeContext, ProximityResult


def window_in_progress(now: datetime.datetime, start: str, end: str) -> bool:
    """Return True if now is between window start and end."""
    h_s, m_s = map(int, start.split(":"))
    h_e, m_e = map(int, end.split(":"))
    start_dt = now.replace(hour=h_s, minute=m_s, second=0, microsecond=0)
    end_dt = now.replace(hour=h_e, minute=m_e, second=0, microsecond=0)
    return start_dt <= now < end_dt


def saved_window_relevant(now: datetime.datetime, saved: dict, idx: int) -> bool:
    """Return True if a saved enabled window is near or already in progress."""
    if not saved.get(f"enabled{idx}"):
        return False
    start = saved.get(f"start{idx}")
    end = saved.get(f"end{idx}")
    if not start or not end:
        return False
    mins = windows.minutes_until(now, start)
    return 0 <= mins <= cfg.WINDOW_LEAD_MINUTES or window_in_progress(now, start, end)


def saved_windows_near_or_active(now: datetime.datetime) -> list[str]:
    """Return saved enabled windows that are near or already in progress."""
    saved = charge_state.get_last_windows()
    if not saved:
        return []

    relevant = []
    if saved_window_relevant(now, saved, 1):
        relevant.append(f"w1={saved['start1']}–{saved['end1']}")
    if saved_window_relevant(now, saved, 2):
        relevant.append(f"w2={saved['start2']}–{saved['end2']}")
    return relevant


def _skip_before_solar(strategy, ctx: ChargeContext) -> ProximityResult:
    s1, e1 = strategy.get_window1(ctx)
    s2, e2 = strategy.get_window2(ctx)
    return ProximityResult(
        should_run=False,
        skip_reason=(f"[SKIP] not near any window  (w1={s1}–{e1}  "
                     f"w2={s2}–{e2}  lead={cfg.WINDOW_LEAD_MINUTES}min)"),
    )


def _skip_after_solar(strategy, ctx: ChargeContext,
                      radiation: int, low_solar: bool) -> ProximityResult:
    s1, e1 = strategy.get_window1(ctx)
    s2, e2 = strategy.get_window2(ctx)
    return ProximityResult(
        should_run=False,
        radiation=radiation,
        low_solar=low_solar,
        skip_reason=(f"[SKIP] not near any window after solar check  "
                     f"(w1={s1}–{e1}  w2={s2}–{e2}  ☀️  "
                     f"lead={cfg.WINDOW_LEAD_MINUTES}min)"),
    )


def proximity_check(now: datetime.datetime, strategy, force: bool, winter: bool,
                    forecast_lat: float, forecast_lon: float) -> ProximityResult:
    """Return whether we should run the full scheduler.

    Phase 0: check saved window state — if we previously enabled a window
             that is near or in progress, don't skip (may need to disable it).
    Phase 1: check against worst-case (earliest) windows — no API calls.
    Phase 2: fetch solar forecast, recheck only if clear day (later windows).
    """
    saved_relevant = [] if force else saved_windows_near_or_active(now)
    if saved_relevant:
        print(f"  Saved window near/active: {', '.join(saved_relevant)} — running full check")
        # Fall through to full run

    ctx_worst = ChargeContext(low_solar=True, soc=None, pv_kw=None, winter=winter)
    if not force and not saved_relevant and not windows.near_window(now, strategy, ctx_worst):
        return _skip_before_solar(strategy, ctx_worst)

    radiation = weather.get_solar_forecast(forecast_lat, forecast_lon)
    low_solar = weather.is_low_solar(radiation, winter)

    ctx_clear = ChargeContext(low_solar=False, soc=None, pv_kw=None, winter=winter)
    if (not force and not low_solar and not saved_relevant
            and not windows.near_window(now, strategy, ctx_clear)):
        return _skip_after_solar(strategy, ctx_clear, radiation, low_solar)

    return ProximityResult(should_run=True, radiation=radiation, low_solar=low_solar)
