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

# ── Settings (from config.py, overridable via .env) ───────────────────────────
from config import (
    TARIFF,
    CHARGE1_START, CHARGE1_END, CHARGE1_ENABLE,
    CHARGE2_START, CHARGE2_END, CHARGE2_ENABLE,
    G13S_WEEKEND_MIDDAY,
    TARGET_SUMMER_WEEKDAY_MORNING, TARGET_SUMMER_WEEKDAY_EVENING,
    TARGET_SUMMER_WEEKEND_MORNING, TARGET_SUMMER_WEEKEND_EVENING,
    TARGET_WINTER_WEEKDAY_MORNING, TARGET_WINTER_WEEKDAY_EVENING,
    TARGET_WINTER_WEEKEND_MORNING, TARGET_WINTER_WEEKEND_EVENING,
    CLOUD_BONUS,
)

BASE_URL = "https://www.foxesscloud.com"


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_winter(date: datetime.date) -> bool:
    """Winter = 1 Oct to 31 Mar"""
    m = date.month
    return m >= 10 or m <= 3


def soc_targets(date: datetime.date, low_solar: bool):
    """Return (morning_target, evening_target) SOC% for the given date.

    Targets represent the minimum SOC needed to cover the next peak block
    without drawing from the grid. Cloud bonus is added when solar is poor.
    Capped at 95% to avoid stressing the battery.
    """
    winter     = is_winter(date)
    is_weekday = date.weekday() < 5
    bonus      = CLOUD_BONUS if low_solar else 0

    if winter:
        if is_weekday:
            morning = TARGET_WINTER_WEEKDAY_MORNING + bonus
            evening = TARGET_WINTER_WEEKDAY_EVENING + bonus
        else:
            morning = TARGET_WINTER_WEEKEND_MORNING + bonus
            evening = TARGET_WINTER_WEEKEND_EVENING + bonus
    else:
        if is_weekday:
            morning = TARGET_SUMMER_WEEKDAY_MORNING + bonus
            evening = TARGET_SUMMER_WEEKDAY_EVENING + bonus
        else:
            morning = TARGET_SUMMER_WEEKEND_MORNING + bonus
            evening = TARGET_SUMMER_WEEKEND_EVENING + bonus

    return min(morning, 95), min(evening, 95)


def g13s_windows(date: datetime.date):
    winter     = is_winter(date)
    is_weekday = date.weekday() < 5

    # Window 1: morning top-up before 07:00 peak
    w1_start, w1_end = "06:30", "07:00"

    # Window 2: pre-evening charge
    # Winter: before 15:00 peak  |  Summer: before 17:00 peak
    if winter:
        w2_start, w2_end = "13:30", "15:00"
    else:
        w2_start, w2_end = "15:30", "17:00"

    # Enable logic:
    # Window 1: weekdays only — weekends have no morning peak in either season
    # Window 2: weekdays always; weekends optional (no peak, but cheap midday rate)
    enable1 = is_weekday
    enable2 = True if is_weekday else G13S_WEEKEND_MIDDAY

    return enable1, w1_start, w1_end, enable2, w2_start, w2_end


def manual_windows(date: datetime.date):
    is_weekday = date.weekday() < 5
    def resolve(policy):
        p = policy.strip().lower()
        if p == "always":   return True
        if p == "never":    return False
        if p == "weekdays": return is_weekday
        if p == "weekends": return not is_weekday
        print(f"  Warning: unknown policy '{p}', defaulting to never")
        return False
    return (resolve(CHARGE1_ENABLE), CHARGE1_START, CHARGE1_END,
            resolve(CHARGE2_ENABLE), CHARGE2_START, CHARGE2_END)


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
        data  = _post("/op/v0/device/real/query", {"sn": sn, "variables": ["SoC"]})
        result = data.get("result", [])
        if not result:
            return None
        datas = result[0].get("datas", [])
        if not datas:
            return None
        return float(datas[0].get("value", 0))
    except Exception as e:
        print(f"Warning: failed to read SOC ({e})")
    return None

def get_cloud_forecast():
    """Return average cloud cover (%) over the next 3 hours from now."""
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude":      FORECAST_LAT,
            "longitude":     FORECAST_LON,
            "hourly":        "cloud_cover",
            "forecast_days": 1,
            "timezone":      "auto",   # local time index
        }, timeout=10)
        r.raise_for_status()
        current_hour = datetime.datetime.now().hour
        cloud = r.json()["hourly"]["cloud_cover"][current_hour:current_hour + 3]
        if not cloud:
            return 0
        avg = sum(cloud) / len(cloud)
        print(f"  Cloud   : {avg:.0f}%  (hours {current_hour}–{current_hour + len(cloud) - 1} local, {len(cloud)}h avg)")
        return avg
    except Exception as e:
        print(f"Warning: cloud forecast failed ({e})")
        return 0  # assume clear on failure


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[RUN] {datetime.datetime.now().isoformat()}")
    if not API_KEY:
        print("ERROR: FOXESS_API_KEY is not set.")
        print("Get your key: foxesscloud.com -> Avatar -> Personal Centre -> API Management")
        print("Then add to .env: FOXESS_API_KEY=your_key_here")
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

    cloud = get_cloud_forecast()

    if TARIFF == "g13s":
        enable1, start1, end1, enable2, start2, end2 = g13s_windows(today)
        season     = "WINTER" if is_winter(today) else "SUMMER"
        day_type   = "weekday" if today.weekday() < 5 else "weekend"
        mode_label = f"Tauron G13s  season={season}  day={day_type}"
    else:
        enable1, start1, end1, enable2, start2, end2 = manual_windows(today)
        day_type   = "weekday" if today.weekday() < 5 else "weekend"
        mode_label = f"Manual  day={day_type}"

    # low_solar: >60% cloud generally overcast; relax to >80% in winter since
    # charging relies less on expected solar refill during short winter days
    low_solar = (cloud > 60) or (is_winter(today) and cloud > 80)

    morning_target, evening_target = soc_targets(today, low_solar)

    enable1 = enable1 and (soc < morning_target)
    enable2 = enable2 and (soc < evening_target)

    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Mode    : {mode_label}")
    print(f"  SOC     : {soc:.1f}%  (morning target={morning_target}%  evening target={evening_target}%{'  +cloud bonus' if low_solar else ''})")
    print(f"  Window 1: {start1}-{end1}  -> {'ENABLE' if enable1 else 'DISABLE'}")
    print(f"  Window 2: {start2}-{end2}  -> {'ENABLE' if enable2 else 'DISABLE'}")
    print()

    try:
        cur      = get_charge_settings(sn)
        already1 = cur.get("enable1")
        already2 = cur.get("enable2")
        print(f"  Current : window1={already1}  window2={already2}")
        if already1 == enable1 and already2 == enable2:
            print("  Already correct -- nothing to do.")
            return
    except Exception as e:
        print(f"  Warning: could not read current state ({e}) -- applying anyway.")

    set_charge_windows(sn, enable1, start1, end1, enable2, start2, end2)
    print(f"  Done: window1={'ENABLED' if enable1 else 'DISABLED'}  window2={'ENABLED' if enable2 else 'DISABLED'}")


if __name__ == "__main__":
    main()
