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
import weather
import charge_state
from context import ChargeContext

api.API_KEY = API_KEY


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now   = datetime.datetime.now()
    force = "--force" in sys.argv
    print(f"[RUN] {now.isoformat()}{' [FORCED]' if force else ''}")

    # ── Skip outside active hours ─────────────────────────────────────────────
    if not force and not (cfg.ACTIVE_HOUR_START <= now.hour < cfg.ACTIVE_HOUR_END):
        print(f"[SKIP] outside active hours ({cfg.ACTIVE_HOUR_START}:00–{cfg.ACTIVE_HOUR_END}:00)")
        sys.exit(0)

    # ── Skip if window is already active — FoxESS manages charge internally ──
    # Avoids API calls and oscillating notifications (99%↔100%) while charging.
    if not force and charge_state.is_active(now):
        print(f"[SKIP] charge window active — FoxESS managing charge internally")
        sys.exit(0)

    # ── Get strategy ──────────────────────────────────────────────────────────
    today    = now.date()
    strategy = strategies.get_strategy(today, cfg.TARIFF)
    winter   = today.month >= 10 or today.month <= 3

    # ── Build a minimal context for proximity checks (soc/pv unknown yet) ────
    # Phase 1 uses low_solar=True (worst case = earliest windows).
    # We don't have SOC/PV yet — dynamic strategies fall back to static windows.
    ctx_worst = ChargeContext(low_solar=True, soc=None, pv_kw=None, winter=winter)

    # ── Phase 1: quick proximity check — no API calls ─────────────────────────
    if not force and not windows.near_window(now, strategy, ctx_worst):
        s1, e1 = strategy.get_window1(ctx_worst)
        s2, e2 = strategy.get_window2(ctx_worst)
        print(f"[SKIP] not near any window  "
              f"(w1={s1}–{e1}  w2={s2}–{e2}  lead={cfg.WINDOW_LEAD_MINUTES}min)")
        sys.exit(0)

    # ── Phase 2: fetch solar forecast ────────────────────────────────────────
    radiation = weather.get_solar_forecast(FORECAST_LAT, FORECAST_LON)
    low_solar = weather.is_low_solar(radiation, winter)

    # Recheck with clear-day windows if not low_solar (they start later)
    ctx_clear = ChargeContext(low_solar=False, soc=None, pv_kw=None, winter=winter)
    if not force and not low_solar and not windows.near_window(now, strategy, ctx_clear):
        s1, e1 = strategy.get_window1(ctx_clear)
        s2, e2 = strategy.get_window2(ctx_clear)
        print(f"[SKIP] not near any window after solar check  "
              f"(w1={s1}–{e1}  w2={s2}–{e2}  ☀️  lead={cfg.WINDOW_LEAD_MINUTES}min)")
        sys.exit(0)

    # ── Validate API key ──────────────────────────────────────────────────────
    if not API_KEY:
        msg = "FOXESS_API_KEY is not set. Get your key at foxesscloud.com → Personal Centre → API Management"
        print(f"ERROR: {msg}")
        notifier.notify_error("Missing API key", Exception(msg))
        sys.exit(1)

    # ── Resolve device SN ─────────────────────────────────────────────────────
    sn = DEVICE_SN
    if not sn or sn.lower() == "auto":
        print("FOXESS_SN not set -- auto-detecting...")
        sn = api.get_first_sn()

    # ── Read SOC and PV in one call ───────────────────────────────────────────
    soc, pv_kw = api.get_device_data(sn)
    if soc is None:
        print("SOC unknown -- forcing SOC=0 (charging will be enabled)")
        soc = 0
    if pv_kw is None:
        print("PV unknown -- assuming 0 kW")
        pv_kw = 0.0

    # ── Build full context ────────────────────────────────────────────────────
    ctx = ChargeContext(low_solar=low_solar, soc=soc, pv_kw=pv_kw, winter=winter)

    # ── Determine windows and targets ─────────────────────────────────────────
    start1, end1   = strategy.get_window1(ctx)
    start2, end2   = strategy.get_window2(ctx)
    morning_target = strategy.morning_target(ctx)
    evening_target = strategy.evening_target(ctx)

    enable1 = strategy.enable1() and (soc < morning_target)
    enable2 = strategy.enable2() and (soc < evening_target)

    # ── Freeze windows outside their active period ────────────────────────────
    if windows.is_closed(now, end1) or windows.is_not_opened_yet(now, start1):
        enable1 = None
    if windows.is_closed(now, end2) or windows.is_not_opened_yet(now, start2):
        enable2 = None

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Strategy: {strategy.name}{'  +cloud bonus' if low_solar else ''}")
    print(f"  SOC     : {soc:.1f}%  (morning target={morning_target}%  evening target={evening_target}%)")
    print(f"  Window 1: {start1}–{end1}  -> {windows.window_status(now, enable1, start1, end1)}")
    print(f"  Window 2: {start2}–{end2}  -> {windows.window_status(now, enable2, start2, end2)}")
    print()

    # ── Read current state and apply if needed ────────────────────────────────
    changed = False
    try:
        cur      = api.get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")

        if enable1 is None: enable1 = already1
        if enable2 is None: enable2 = already2

        print(f"  Current : window1={already1}  window2={already2}")
        if already1 == enable1 and already2 == enable2:
            print("  Already correct -- nothing to do.")
        else:
            api.set_charge_windows(sn, enable1, start1, end1, enable2, start2, end2)
            print(f"  Done: window1={'ENABLED' if enable1 else 'DISABLED'}  "
                  f"window2={'ENABLED' if enable2 else 'DISABLED'}")
            changed = True

            # ── Persist charge state ──────────────────────────────────────────
            # Only skip subsequent runs when target is 100% — FoxESS handles
            # the charge limit itself and will stop naturally.
            # For other targets (e.g. 85%) we keep checking SOC every 2 min
            # so we can disable the window when the target is reached.
            if enable1 and not enable2 and morning_target == 100:
                charge_state.save(end1)
            elif enable2 and not enable1 and evening_target == 100:
                charge_state.save(end2)
            elif enable1 and enable2:
                if morning_target == 100 and evening_target == 100:
                    charge_state.save(max(end1, end2))
            else:
                # Windows disabled or target < 100% — clear any saved state
                charge_state.clear()

    except Exception as e:
        notifier.notify_error("Failed to read/set charge windows", e)
        print(f"  Error: {e}")
        return

    # ── Notify ────────────────────────────────────────────────────────────────
    notifier.notify_run(
        sn=sn,
        strategy_name=strategy.name,
        soc=soc,
        radiation=radiation,
        low_solar=low_solar,
        morning_target=morning_target,
        evening_target=evening_target,
        enable1=enable1, enable2=enable2,
        start1=start1, end1=end1,
        start2=start2, end2=end2,
        changed=changed,
    )


if __name__ == "__main__":
    main()
