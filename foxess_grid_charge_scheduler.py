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
from weather import get_cloud_forecast, is_low_solar

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




# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[RUN] {datetime.datetime.now().isoformat()}")

    # ── Skip outside active hours ─────────────────────────────────────────────
    now_h = datetime.datetime.now().hour
    if not (cfg.ACTIVE_HOUR_START <= now_h < cfg.ACTIVE_HOUR_END):
        print(f"[SKIP] outside active hours ({cfg.ACTIVE_HOUR_START}:00–{cfg.ACTIVE_HOUR_END}:00)")
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

    today = datetime.date.today()
    soc   = get_battery_soc(sn)
    if soc is None:
        print("SOC unknown -- forcing SOC=0 (charging will be enabled)")
        soc = 0

    cloud     = get_cloud_forecast(FORECAST_LAT, FORECAST_LON)
    winter    = today.month >= 10 or today.month <= 3
    low_solar = is_low_solar(cloud, winter)

    # ── Strategy selects windows and targets for today ────────────────────────
    strategy = get_strategy(today, cfg.TARIFF)

    start1, end1   = strategy.window1
    start2, end2   = strategy.window2
    morning_target = strategy.morning_target(low_solar)
    evening_target = strategy.evening_target(low_solar)

    enable1 = strategy.enable1() and (soc < morning_target)
    enable2 = strategy.enable2() and (soc < evening_target)

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Strategy: {strategy.name}{'  +cloud bonus' if low_solar else ''}")
    print(f"  SOC     : {soc:.1f}%  (morning target={morning_target}%  evening target={evening_target}%)")
    print(f"  Window 1: {start1}-{end1}  -> {'ENABLE' if enable1 else 'DISABLE'}")
    print(f"  Window 2: {start2}-{end2}  -> {'ENABLE' if enable2 else 'DISABLE'}")
    print()

    # ── Check current state and apply if needed ───────────────────────────────
    changed = False
    try:
        cur      = get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")
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
        cloud=cloud,
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
