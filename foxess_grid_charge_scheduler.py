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
import charge_state
import savings
from proximity import proximity_check, window_in_progress
from scheduler_models import ChargeContext, ChargePlan, ChargeWindow

api.API_KEY = API_KEY


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

    if already1 and window_in_progress(now, cur_start1, cur_end1):
        plan.window1.start, plan.window1.end = cur_start1, cur_end1
        print(f"  Window 1 in progress — keeping API times ({cur_start1}–{cur_end1})")
    if already2 and window_in_progress(now, cur_start2, cur_end2):
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
        _record_savings(plan, already1, already2, ctx.soc, ctx.winter,
                         today.weekday() >= 5, strategy.name)

    except Exception as e:
        notifier.notify_error("Failed to read/set charge windows", e)
        print(f"  Error: {e}")
        return

    _notify_run(sn, ctx, strategy, plan, radiation, changed)


def _update_charge_state(plan: ChargePlan):
    """Persist sent windows, and separately mark target=100% skip windows.

    A window qualifies for skip if it is enabled AND its target is 100%
    (FoxESS self-manages the charge limit and will stop when full).
    Each window is evaluated independently so mixed-target plans
    (e.g. morning_target=100 + evening_target=85) are handled correctly.
    """
    w1 = plan.window1
    w2 = plan.window2
    charge_state.save_windows(w1.start, w1.end, w1.enabled, w2.start, w2.end, w2.enabled)

    skip_end = None
    if w1.enabled and plan.morning_target == 100:
        skip_end = w1.end
    if w2.enabled and plan.evening_target == 100:
        skip_end = max(skip_end, w2.end) if skip_end else w2.end

    if skip_end:
        charge_state.save_skip(skip_end)
    else:
        charge_state.clear_skip()


def _record_savings(plan: "ChargePlan", prev_enabled1: bool, prev_enabled2: bool,
                    soc: float | None, winter: bool, weekend: bool,
                    strategy_name: str) -> None:
    """Record a session when a window transitions from enabled → disabled."""
    w1, w2 = plan.window1, plan.window2
    # Window was on, now off (or frozen) → session completed
    if prev_enabled1 and not w1.enabled:
        savings.record_session(1, w1.start, w1.end, soc, winter, strategy_name, weekend)
    if prev_enabled2 and not w2.enabled:
        savings.record_session(2, w2.start, w2.end, soc, winter, strategy_name, weekend)


# ── Main ──────────────────────────────────────────────────────────────────────────
def main():
    now   = datetime.datetime.now()
    force = "--force" in sys.argv
    print(f"[RUN] {now.isoformat()}{' [FORCED]' if force else ''}")

    if _should_skip_early(now, force):
        sys.exit(0)

    # ── Savings report mode ──────────────────────────────────────────────────
    savings_arg = next((a for a in sys.argv[1:] if a.startswith("--savings")), None)
    if savings_arg:
        period = savings_arg.split("=")[1] if "=" in savings_arg else "30d"
        savings.print_report(period)
        from notifier import _send
        embed = savings.discord_report(period)
        if embed:
            _send(embed)
        sys.exit(0)

    today    = now.date()
    strategy = strategies.get_strategy(today, cfg.TARIFF)
    winter   = today.month >= 10 or today.month <= 3

    proximity = proximity_check(now, strategy, force, winter, FORECAST_LAT, FORECAST_LON)
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
          f"{windows.window_status(now, plan.window1.enabled, plan.window1.start, plan.window1.end, force)}")
    print(f"  Window 2: {plan.window2.start}–{plan.window2.end}  -> "
          f"{windows.window_status(now, plan.window2.enabled, plan.window2.start, plan.window2.end, force)}")
    print()

    _apply(sn, now, ctx, strategy, plan, radiation)


if __name__ == "__main__":
    main()
