"""
FoxESS Grid Charge Scheduler

Automatically manages battery grid charging windows on a FoxESS inverter,
optimised for the Tauron G13s time-of-use tariff (Poland).

Runs via cron every 2 minutes but exits immediately unless near a charge
window — no unnecessary API calls.
"""
import datetime
import os
import sys
from dataclasses import dataclass
from pathlib import Path


# ── Load secrets from .env ────────────────────────────────────────────────────
def load_dotenv(path=".env"):
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

env_path = Path(__file__).parent / ".env"
if not env_path.exists():
    print("Warning: .env not found — secrets must be set as environment variables")
load_dotenv(env_path)

# ── Secrets (from .env only) ──────────────────────────────────────────────────
API_KEY      = os.getenv("FOXESS_API_KEY", "")
DEVICE_SN    = os.getenv("FOXESS_SN", "")
FORECAST_LAT = float(os.getenv("FOXESS_LAT", "50.2849"))
FORECAST_LON = float(os.getenv("FOXESS_LON", "18.6717"))

# ── Modules ───────────────────────────────────────────────────────────────────
import config as cfg
import foxess_api as api
import windows
import strategies
import notifier
import weather
import charge_state
from context import ChargeContext

api.API_KEY = API_KEY


# ── Charge plan ───────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _should_skip_early(now: datetime.datetime, force: bool) -> bool:
    """Return True and print reason if we should exit before any API calls."""
    if not force and not (cfg.ACTIVE_HOUR_START <= now.hour < cfg.ACTIVE_HOUR_END):
        print(f"[SKIP] outside active hours ({cfg.ACTIVE_HOUR_START}:00–{cfg.ACTIVE_HOUR_END}:00)")
        return True
    if not force and charge_state.should_skip(now):
        print("[SKIP] charge window active — FoxESS managing charge internally")
        return True
    return False


def _saved_windows_near_or_active(now: datetime.datetime) -> list[str]:
    """Return saved enabled windows that are near or already in progress."""
    saved = charge_state.get_last_windows()
    if not saved:
        return []

    relevant = []
    if _saved_window_relevant(now, saved, 1):
        relevant.append(f"w1={saved['start1']}–{saved['end1']}")
    if _saved_window_relevant(now, saved, 2):
        relevant.append(f"w2={saved['start2']}–{saved['end2']}")
    return relevant


def _proximity_check(now: datetime.datetime, strategy, force: bool, winter: bool) -> ProximityResult:
    """Two-phase proximity check. Returns whether we should run the full scheduler.

    Phase 0: check saved window state — if we previously enabled a window
             that is near or in progress, don't skip (may need to disable it).
    Phase 1: check against worst-case (earliest) windows — no API calls.
    Phase 2: fetch solar forecast, recheck only if clear day (later windows).
    """
    saved_relevant = [] if force else _saved_windows_near_or_active(now)
    if saved_relevant:
        print(f"  Saved window near/active: {', '.join(saved_relevant)} — running full check")
        # Fall through to full run

    ctx_worst = ChargeContext(low_solar=True, soc=None, pv_kw=None, winter=winter)
    if not force and not windows.near_window(now, strategy, ctx_worst):
        # Only skip if no saved enabled window is near or active.
        if not saved_relevant:
            s1, e1 = strategy.get_window1(ctx_worst)
            s2, e2 = strategy.get_window2(ctx_worst)
            return ProximityResult(
                should_run=False,
                skip_reason=(f"[SKIP] not near any window  (w1={s1}–{e1}  "
                             f"w2={s2}–{e2}  lead={cfg.WINDOW_LEAD_MINUTES}min)"),
            )

    radiation = weather.get_solar_forecast(FORECAST_LAT, FORECAST_LON)
    low_solar = weather.is_low_solar(radiation, winter)

    if not force and not low_solar:
        ctx_clear = ChargeContext(low_solar=False, soc=None, pv_kw=None, winter=winter)
        if not windows.near_window(now, strategy, ctx_clear):
            if not saved_relevant:
                s1, e1 = strategy.get_window1(ctx_clear)
                s2, e2 = strategy.get_window2(ctx_clear)
                return ProximityResult(
                    should_run=False,
                    radiation=radiation,
                    low_solar=low_solar,
                    skip_reason=(f"[SKIP] not near any window after solar check  "
                                 f"(w1={s1}–{e1}  w2={s2}–{e2}  ☀️  "
                                 f"lead={cfg.WINDOW_LEAD_MINUTES}min)"),
                )

    return ProximityResult(should_run=True, radiation=radiation, low_solar=low_solar)


def _resolve_sn() -> str:
    """Return device SN from env or auto-detect via API."""
    sn = DEVICE_SN
    if not sn or sn.lower() == "auto":
        print("FOXESS_SN not set -- auto-detecting...")
        sn = api.get_first_sn()
    return sn


def _read_device(sn: str) -> tuple:
    """Read SOC and PV from device. Returns (soc, pv_kw) with safe fallbacks."""
    soc, pv_kw = api.get_device_data(sn)
    if soc is None:
        print("SOC unknown -- forcing SOC=0 (charging will be enabled)")
        soc = 0.0
    if pv_kw is None:
        print("PV unknown -- assuming 0 kW")
        pv_kw = 0.0
    return soc, pv_kw


def _window_in_progress(now: datetime.datetime, start: str, end: str) -> bool:
    """Return True if now is between window start and end."""
    h_s, m_s = map(int, start.split(":"))
    h_e, m_e = map(int, end.split(":"))
    start_dt = now.replace(hour=h_s, minute=m_s, second=0, microsecond=0)
    end_dt   = now.replace(hour=h_e, minute=m_e, second=0, microsecond=0)
    return start_dt <= now < end_dt


def _saved_window_relevant(now: datetime.datetime, saved: dict, idx: int) -> bool:
    """Return True if a saved enabled window is near or already in progress."""
    if not saved.get(f"enabled{idx}"):
        return False
    start = saved.get(f"start{idx}")
    end = saved.get(f"end{idx}")
    if not start or not end:
        return False
    mins = windows.minutes_until(now, start)
    return 0 <= mins <= cfg.WINDOW_LEAD_MINUTES or _window_in_progress(now, start, end)


def _evaluate_windows(now: datetime.datetime, strategy, ctx: ChargeContext,
                      force: bool = False) -> ChargePlan:
    """Determine window times, targets and enable flags from strategy + context.

    enable=None means frozen (outside active period) — bypassed when force=True.
    """
    start1, end1   = strategy.get_window1(ctx)
    start2, end2   = strategy.get_window2(ctx)
    morning_target = strategy.morning_target(ctx)
    evening_target = strategy.evening_target(ctx)

    enable1 = strategy.enable1() and (ctx.soc < morning_target)
    enable2 = strategy.enable2() and (ctx.soc < evening_target)

    if not force:
        if windows.is_closed(now, end1) or windows.is_not_opened_yet(now, start1):
            enable1 = None
        if windows.is_closed(now, end2) or windows.is_not_opened_yet(now, start2):
            enable2 = None

    return ChargePlan(
        window1=ChargeWindow(start1, end1, enable1),
        window2=ChargeWindow(start2, end2, enable2),
        morning_target=morning_target,
        evening_target=evening_target,
    )


def _api_time(cur: dict, key: str) -> str:
    t = cur.get(key, {})
    return f"{t.get('hour', 0):02d}:{t.get('minute', 0):02d}"


def _use_api_window_if_frozen(window: ChargeWindow, cur: dict, idx: int, already: bool) -> None:
    """Frozen windows keep current API enable state and API times."""
    if window.enabled is not None:
        return
    window.enabled = already
    start = cur.get(f"startTime{idx}", {})
    end = cur.get(f"endTime{idx}", {})
    if start and end:
        window.start = f"{start.get('hour', 0):02d}:{start.get('minute', 0):02d}"
        window.end = f"{end.get('hour', 0):02d}:{end.get('minute', 0):02d}"


def _keep_api_times_if_in_progress(now: datetime.datetime, plan: ChargePlan,
                                   cur: dict, already1: bool, already2: bool) -> None:
    """Keep API times for active windows to avoid dynamic recalculation churn."""
    cur_start1, cur_end1 = _api_time(cur, "startTime1"), _api_time(cur, "endTime1")
    cur_start2, cur_end2 = _api_time(cur, "startTime2"), _api_time(cur, "endTime2")

    if already1 and _window_in_progress(now, cur_start1, cur_end1):
        plan.window1.start, plan.window1.end = cur_start1, cur_end1
        print(f"  Window 1 in progress — keeping API times ({cur_start1}–{cur_end1})")
    if already2 and _window_in_progress(now, cur_start2, cur_end2):
        plan.window2.start, plan.window2.end = cur_start2, cur_end2
        print(f"  Window 2 in progress — keeping API times ({cur_start2}–{cur_end2})")


def _times_match_enabled_windows(plan: ChargePlan, cur: dict,
                                 already1: bool, already2: bool) -> bool:
    """Compare times only for currently enabled windows."""
    w1 = plan.window1
    w2 = plan.window2
    matches = True
    if w1.enabled and already1:
        matches = matches and (
            _api_time(cur, "startTime1") == w1.start and _api_time(cur, "endTime1") == w1.end
        )
    if w2.enabled and already2:
        matches = matches and (
            _api_time(cur, "startTime2") == w2.start and _api_time(cur, "endTime2") == w2.end
        )
    return matches


def _log_time_changes(plan: ChargePlan, cur: dict, already1: bool, already2: bool) -> None:
    w1 = plan.window1
    w2 = plan.window2
    cur_start1, cur_end1 = _api_time(cur, "startTime1"), _api_time(cur, "endTime1")
    cur_start2, cur_end2 = _api_time(cur, "startTime2"), _api_time(cur, "endTime2")

    if w1.enabled and already1 and (cur_start1 != w1.start or cur_end1 != w1.end):
        print(f"  Times changed: w1 {cur_start1}–{cur_end1}→{w1.start}–{w1.end}")
    if w2.enabled and already2 and (cur_start2 != w2.start or cur_end2 != w2.end):
        print(f"  Times changed: w2 {cur_start2}–{cur_end2}→{w2.start}–{w2.end}")


def _send_plan(sn: str, plan: ChargePlan) -> None:
    w1 = plan.window1
    w2 = plan.window2
    api.set_charge_windows(sn, w1.enabled, w1.start, w1.end,
                           w2.enabled, w2.start, w2.end)
    print(f"  Done: window1={'ENABLED' if w1.enabled else 'DISABLED'}  "
          f"window2={'ENABLED' if w2.enabled else 'DISABLED'}")


def _notify_run(sn, ctx, strategy, plan: ChargePlan, radiation, changed: bool) -> None:
    notifier.notify_run(
        sn=sn,
        strategy_name=strategy.name,
        soc=ctx.soc,
        radiation=radiation,
        low_solar=ctx.low_solar,
        morning_target=plan.morning_target,
        evening_target=plan.evening_target,
        enable1=plan.window1.enabled, enable2=plan.window2.enabled,
        start1=plan.window1.start, end1=plan.window1.end,
        start2=plan.window2.start, end2=plan.window2.end,
        changed=changed,
    )


def _apply(sn, now, ctx, strategy, plan: ChargePlan, radiation) -> None:
    """Read current state, apply changes if needed, save state and notify."""
    changed = False
    try:
        cur = api.get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")

        _use_api_window_if_frozen(plan.window1, cur, 1, already1)
        _use_api_window_if_frozen(plan.window2, cur, 2, already2)
        print(f"  Current : window1={already1}  window2={already2}")

        _keep_api_times_if_in_progress(now, plan, cur, already1, already2)
        times_match = _times_match_enabled_windows(plan, cur, already1, already2)

        if already1 == plan.window1.enabled and already2 == plan.window2.enabled and times_match:
            print("  Already correct -- nothing to do.")
        else:
            if not times_match:
                _log_time_changes(plan, cur, already1, already2)
            _send_plan(sn, plan)
            changed = True

        _update_charge_state(plan)

    except Exception as e:
        notifier.notify_error("Failed to read/set charge windows", e)
        print(f"  Error: {e}")
        return

    _notify_run(sn, ctx, strategy, plan, radiation, changed)


def _update_charge_state(plan: ChargePlan):
    """Persist sent windows, and separately mark target=100% skip windows."""
    w1 = plan.window1
    w2 = plan.window2
    charge_state.save_windows(w1.start, w1.end, w1.enabled, w2.start, w2.end, w2.enabled)

    if w1.enabled and not w2.enabled and plan.morning_target == 100:
        charge_state.save_skip(w1.end)
    elif w2.enabled and not w1.enabled and plan.evening_target == 100:
        charge_state.save_skip(w2.end)
    elif w1.enabled and w2.enabled and plan.morning_target == 100 and plan.evening_target == 100:
        charge_state.save_skip(max(w1.end, w2.end))
    else:
        charge_state.clear_skip()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now   = datetime.datetime.now()
    force = "--force" in sys.argv
    print(f"[RUN] {now.isoformat()}{' [FORCED]' if force else ''}")

    if _should_skip_early(now, force):
        sys.exit(0)

    today    = now.date()
    strategy = strategies.get_strategy(today, cfg.TARIFF)
    winter   = today.month >= 10 or today.month <= 3

    proximity = _proximity_check(now, strategy, force, winter)
    if not proximity.should_run:
        print(proximity.skip_reason)
        sys.exit(0)

    radiation = proximity.radiation
    low_solar = proximity.low_solar

    if not API_KEY:
        msg = "FOXESS_API_KEY is not set. Get your key at foxesscloud.com → Personal Centre → API Management"
        print(f"ERROR: {msg}")
        notifier.notify_error("Missing API key", Exception(msg))
        sys.exit(1)

    sn          = _resolve_sn()
    soc, pv_kw  = _read_device(sn)
    ctx         = ChargeContext(low_solar=low_solar, soc=soc, pv_kw=pv_kw, winter=winter)

    plan = _evaluate_windows(now, strategy, ctx, force)

    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Strategy: {strategy.name}{'  +cloud bonus' if low_solar else ''}")
    print(f"  SOC     : {soc:.1f}%  (morning target={plan.morning_target}%  "
          f"evening target={plan.evening_target}%)")
    print(f"  Window 1: {plan.window1.start}–{plan.window1.end}  -> "
          f"{windows.window_status(now, plan.window1.enabled, plan.window1.start, plan.window1.end)}")
    print(f"  Window 2: {plan.window2.start}–{plan.window2.end}  -> "
          f"{windows.window_status(now, plan.window2.enabled, plan.window2.start, plan.window2.end)}")
    print()

    _apply(sn, now, ctx, strategy, plan, radiation)


if __name__ == "__main__":
    main()
