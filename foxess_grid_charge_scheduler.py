import hashlib, time, datetime, os, sys, requests
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

# ── Settings, strategy and notifier ──────────────────────────────────────────
import config as cfg
from strategies import get_strategy
from notifier import notify_run, notify_error, notify_warning
from weather import get_solar_forecast, is_low_solar

BASE_URL = "https://www.foxesscloud.com"


# ── API helpers ───────────────────────────────────────────────────────────────
def _headers(path):
    ts  = str(round(time.time() * 1000))
    sig = hashlib.md5(rf"{path}\r\n{API_KEY}\r\n{ts}".encode()).hexdigest()
    return {"token": API_KEY, "timestamp": ts, "signature": sig,
            "lang": "en", "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 foxess-scheduler/1.0"}

def _get(path, params=None):
    r = requests.get(BASE_URL + path, headers=_headers(path), params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("errno", 0) != 0:
        raise RuntimeError(f"API [{d.get('errno')}]: {d.get('msg', d)}")
    return d

def _post(path, body):
    r = requests.post(BASE_URL + path, headers=_headers(path), json=body, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("errno", 0) != 0:
        raise RuntimeError(f"API [{d.get('errno')}]: {d.get('msg', d)}")
    return d

def get_first_sn():
    data    = _post("/op/v0/device/list", {"currentPage": 1, "pageSize": 10})
    devices = data.get("result", {}).get("data", [])
    if not devices:
        raise RuntimeError("No devices found on this account.")
    sn = devices[0]["deviceSN"]
    print(f"  Auto-detected SN: {sn}")
    if len(devices) > 1:
        print(f"  ({len(devices)} devices found -- using first. Set FOXESS_SN to choose.)")
    return sn

def get_charge_settings(sn):
    return _get("/op/v0/device/battery/forceChargeTime/get", {"sn": sn}).get("result", {})

def set_charge_windows(sn, enable1, start1, end1, enable2, start2, end2):
    def t(s):
        h, m = map(int, s.split(":"))
        return {"hour": h, "minute": m}
    return _post("/op/v0/device/battery/forceChargeTime/set", {
        "sn": sn,
        "enable1": enable1, "startTime1": t(start1), "endTime1": t(end1),
        "enable2": enable2, "startTime2": t(start2), "endTime2": t(end2),
    })

def get_battery_soc(sn):
    try:
        data   = _post("/op/v0/device/real/query", {"sn": sn, "variables": ["SoC"]})
        result = data.get("result", [])
        if not result:
            return None
        datas  = result[0].get("datas", [])
        if not datas:
            return None
        return float(datas[0].get("value", 0))
    except Exception as e:
        notify_warning(f"Failed to read SOC: {e}")
        print(f"Warning: failed to read SOC ({e})")
    return None

def _minutes_until(now: datetime.datetime, window_start: str) -> int:
    """Return minutes from now until window_start. Negative if already past."""
    h, m = map(int, window_start.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return int((target - now).total_seconds() / 60)


def _near_window(now: datetime.datetime, strategy) -> bool:
    """Return True if now is within WINDOW_LEAD_MINUTES before either window start,
    or inside the window itself."""
    lead = cfg.WINDOW_LEAD_MINUTES
    for start, end in (strategy.window1, strategy.window2):
        mins = _minutes_until(now, start)
        h_end, m_end = map(int, end.split(":"))
        end_dt = now.replace(hour=h_end, minute=m_end, second=0, microsecond=0)
        inside = now <= end_dt
        if -lead <= mins <= lead and inside or (0 >= mins and inside):
            return True
        if 0 <= mins <= lead:
            return True
    return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now   = datetime.datetime.now()
    force = "--force" in sys.argv
    print(f"[RUN] {now.isoformat()}{' [FORCED]' if force else ''}")

    # ── Skip outside active hours ─────────────────────────────────────────────
    if not force and not (cfg.ACTIVE_HOUR_START <= now.hour < cfg.ACTIVE_HOUR_END):
        print(f"[SKIP] outside active hours ({cfg.ACTIVE_HOUR_START}:00–{cfg.ACTIVE_HOUR_END}:00)")
        sys.exit(0)

    # ── Get strategy early so we can check window proximity ──────────────────
    today    = now.date()
    strategy = get_strategy(today, cfg.TARIFF)

    if not force and not _near_window(now, strategy):
        start1, end1 = strategy.window1
        start2, end2 = strategy.window2
        print(f"[SKIP] not near any window  (w1={start1}–{end1}  w2={start2}–{end2}  lead={cfg.WINDOW_LEAD_MINUTES}min)")
        sys.exit(0)

    if not API_KEY:
        msg = "FOXESS_API_KEY is not set. Get your key at foxesscloud.com → Personal Centre → API Management"
        print(f"ERROR: {msg}")
        notify_error("Missing API key", Exception(msg))
        sys.exit(1)

    sn = DEVICE_SN
    if not sn or sn.lower() == "auto":
        print("FOXESS_SN not set -- auto-detecting...")
        sn = get_first_sn()

    today = now.date()
    soc   = get_battery_soc(sn)
    if soc is None:
        print("SOC unknown -- forcing SOC=0 (charging will be enabled)")
        soc = 0

    radiation = get_solar_forecast(FORECAST_LAT, FORECAST_LON)
    winter    = today.month >= 10 or today.month <= 3
    low_solar = is_low_solar(radiation, winter)

    # strategy already selected above for window proximity check

    start1, end1   = strategy.window1
    start2, end2   = strategy.window2
    morning_target = strategy.morning_target(low_solar)
    evening_target = strategy.evening_target(low_solar)

    enable1 = strategy.enable1() and (soc < morning_target)
    enable2 = strategy.enable2() and (soc < evening_target)

    # ── Freeze windows after their end times ─────────────────────────────────
    # Once a window has closed, don't touch its state — it already did its job.
    # Avoids spurious state changes and notifications.
    def _is_closed(end_time: str) -> bool:
        h, m = map(int, end_time.split(":"))
        return now >= now.replace(hour=h, minute=m, second=0, microsecond=0)

    if _is_closed(end1):
        enable1 = None  # frozen
    if _is_closed(end2):
        enable2 = None  # frozen

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Strategy: {strategy.name}{'  +cloud bonus' if low_solar else ''}")
    print(f"  SOC     : {soc:.1f}%  (morning target={morning_target}%  evening target={evening_target}%)")
    print(f"  Window 1: {start1}-{end1}  -> {'ENABLE' if enable1 else 'DISABLE' if enable1 is not None else 'FROZEN (window closed)'}")
    print(f"  Window 2: {start2}-{end2}  -> {'ENABLE' if enable2 else 'DISABLE' if enable2 is not None else 'FROZEN (window closed)'}")
    print()

    # ── Check current state and apply if needed ───────────────────────────────
    changed = False
    try:
        cur      = get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")

        # Frozen windows keep their current state — never trigger a change
        if enable1 is None:
            enable1 = already1
        if enable2 is None:
            enable2 = already2

        print(f"  Current : window1={already1}  window2={already2}")
        if already1 == enable1 and already2 == enable2:
            print("  Already correct -- nothing to do.")
        else:
            set_charge_windows(sn, enable1, start1, end1, enable2, start2, end2)
            print(f"  Done: window1={'ENABLED' if enable1 else 'DISABLED'}  window2={'ENABLED' if enable2 else 'DISABLED'}")
            changed = True
    except Exception as e:
        notify_error("Failed to read/set charge windows", e)
        print(f"  Error: {e}")
        return

    # ── Discord notifications ─────────────────────────────────────────────────
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
