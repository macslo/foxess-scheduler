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
FORECAST_LAT = float(os.getenv("FOXESS_LAT", "50.2849"))  # default: Gliwice
FORECAST_LON = float(os.getenv("FOXESS_LON", "18.6717"))

# ── Modules ───────────────────────────────────────────────────────────────────
import config as cfg
import foxess_api as api
from strategies import get_strategy
from notifier import notify_run, notify_error, notify_warning
from weather import get_solar_forecast, is_low_solar
from windows import near_window, is_closed, is_not_opened_yet, window_status, minutes_until

# Pass API key to the api module
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

    # ── Get strategy ──────────────────────────────────────────────────────────
    today    = now.date()
    strategy = get_strategy(today, cfg.TARIFF)
    winter   = today.month >= 10 or today.month <= 3

    # ── Phase 1: quick proximity check assuming worst case (low_solar=True) ──
    # low_solar=True = earliest possible window starts — if not near even those,
    # skip immediately without calling the weather API.
    if not force and not near_window(now, strategy, True):
        start1, end1 = strategy.get_window1(True)
        start2, end2 = strategy.get_window2(True)
        print(f"[SKIP] not near any window  "
              f"(w1={start1}–{end1}  w2={start2}–{end2}  lead={cfg.WINDOW_LEAD_MINUTES}min)")
        sys.exit(0)

    # ── Phase 2: fetch solar forecast, recheck only if clear day ─────────────
    # low_solar=True: windows already at earliest — phase 1 confirmed proximity.
    # low_solar=False: windows are later — recheck since we may be too early.
    radiation = get_solar_forecast(FORECAST_LAT, FORECAST_LON)
    low_solar = is_low_solar(radiation, winter)

    if not force and not low_solar and not near_window(now, strategy, False):
        start1, end1 = strategy.get_window1(False)
        start2, end2 = strategy.get_window2(False)
        print(f"[SKIP] not near any window after solar check  "
              f"(w1={start1}–{end1}  w2={start2}–{end2}  ☀️  lead={cfg.WINDOW_LEAD_MINUTES}min)")
        sys.exit(0)

    # ── Validate API key ──────────────────────────────────────────────────────
    if not API_KEY:
        msg = "FOXESS_API_KEY is not set. Get your key at foxesscloud.com → Personal Centre → API Management"
        print(f"ERROR: {msg}")
        notify_error("Missing API key", Exception(msg))
        sys.exit(1)

    # ── Resolve device SN ─────────────────────────────────────────────────────
    sn = DEVICE_SN
    if not sn or sn.lower() == "auto":
        print("FOXESS_SN not set -- auto-detecting...")
        sn = api.get_first_sn()

    # ── Read SOC ──────────────────────────────────────────────────────────────
    soc = api.get_battery_soc(sn)
    if soc is None:
        print("SOC unknown -- forcing SOC=0 (charging will be enabled)")
        soc = 0

    # ── Determine windows and targets ─────────────────────────────────────────
    start1, end1   = strategy.get_window1(low_solar)
    start2, end2   = strategy.get_window2(low_solar)
    morning_target = strategy.morning_target(low_solar)
    evening_target = strategy.evening_target(low_solar)

    enable1 = strategy.enable1() and (soc < morning_target)
    enable2 = strategy.enable2() and (soc < evening_target)

    # ── Freeze windows outside their active period ────────────────────────────
    # Only touch a window when near it — not hours before or after.
    if is_closed(now, end1) or is_not_opened_yet(now, start1):
        enable1 = None
    if is_closed(now, end2) or is_not_opened_yet(now, start2):
        enable2 = None

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Strategy: {strategy.name}{'  +cloud bonus' if low_solar else ''}")
    print(f"  SOC     : {soc:.1f}%  (morning target={morning_target}%  evening target={evening_target}%)")
    print(f"  Window 1: {start1}–{end1}  -> {window_status(now, enable1, start1, end1)}")
    print(f"  Window 2: {start2}–{end2}  -> {window_status(now, enable2, start2, end2)}")
    print()

    # ── Read current state ────────────────────────────────────────────────────
    changed = False
    try:
        cur      = api.get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")

        # Frozen windows keep their current state
        if enable1 is None:
            enable1 = already1
        if enable2 is None:
            enable2 = already2

        print(f"  Current : window1={already1}  window2={already2}")
        if already1 == enable1 and already2 == enable2:
            print("  Already correct -- nothing to do.")
        else:
            api.set_charge_windows(sn, enable1, start1, end1, enable2, start2, end2)
            print(f"  Done: window1={'ENABLED' if enable1 else 'DISABLED'}  "
                  f"window2={'ENABLED' if enable2 else 'DISABLED'}")
            changed = True

    except Exception as e:
        notify_error("Failed to read/set charge windows", e)
        print(f"  Error: {e}")
        return

    # ── Notify ────────────────────────────────────────────────────────────────
    notify_run(
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
