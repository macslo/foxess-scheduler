import hashlib, time, datetime, os, sys, json, requests
from pathlib import Path

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
    print(".env not found")
load_dotenv(env_path)

API_KEY   = os.getenv("FOXESS_API_KEY", "")
DEVICE_SN = os.getenv("FOXESS_SN", "")
BASE_URL  = "https://www.foxesscloud.com"

# ── Tariff mode ───────────────────────────────────────────────────────────────
# FOXESS_TARIFF=g13s   -> automatic Tauron G13s seasonal schedule (default)
# FOXESS_TARIFF=manual -> use your own windows defined below
TARIFF = os.getenv("FOXESS_TARIFF", "g13s").strip().lower()

# ── Manual windows (only used when FOXESS_TARIFF=manual) ─────────────────────
CHARGE1_START  = os.getenv("FOXESS_CHARGE1_START",  "01:00")
CHARGE1_END    = os.getenv("FOXESS_CHARGE1_END",    "05:00")
CHARGE1_ENABLE = os.getenv("FOXESS_CHARGE1_ENABLE", "weekdays")
CHARGE2_START  = os.getenv("FOXESS_CHARGE2_START",  "13:00")
CHARGE2_END    = os.getenv("FOXESS_CHARGE2_END",    "15:00")
CHARGE2_ENABLE = os.getenv("FOXESS_CHARGE2_ENABLE", "never")

# ── Location (used for cloud cover forecast) ──────────────────────────────────
# Defaults to Gliwice, Poland. Override in .env with your exact location:
#   FOXESS_LAT=50.2849
#   FOXESS_LON=18.6717
FORECAST_LAT = float(os.getenv("FOXESS_LAT", "50.2849"))
FORECAST_LON = float(os.getenv("FOXESS_LON", "18.6717"))

# ── Tauron G13s schedule (source: tauron.pl/dla-domu/prad/prad-z-usluga/tanie-godziny)
#
# WINTER (1 Oct – 31 Mar)
#   Weekdays & weekends:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6150 zł/kWh)
#     🟡 CHEAP midday:  10:00 – 15:00  (0.7196 zł/kWh weekdays / 0.4317 weekends)
#     🔴 PEAK:          07:00 – 10:00  and  15:00 – 21:00  <- do NOT charge
#
# SUMMER (1 Apr – 30 Sep)
#   Weekdays:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6519 zł/kWh)
#     🟢 CHEAPEST mid:  09:00 – 17:00  (0.5228 zł/kWh)  <- solar hours!
#     🔴 PEAK:          07:00 – 09:00  and  17:00 – 21:00  <- do NOT charge
#   Weekends:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6519 zł/kWh)
#     🟢 CHEAPEST mid:  09:00 – 17:00  (0.2091 zł/kWh!)  <- very cheap
#     🟡 MEDIUM:        07:00 – 09:00  and  17:00 – 21:00  (0.4474 zł/kWh)
#
# Strategy: always charge in night window; charge midday on weekdays (avoids peak);
#           weekend midday optional (whole day is cheap anyway, configurable below)

G13S_WEEKEND_MIDDAY = os.getenv("FOXESS_G13S_WEEKEND_MIDDAY", "false").strip().lower() == "true"


def is_winter(date: datetime.date) -> bool:
    """Winter = 1 Oct to 31 Mar"""
    m = date.month
    return m >= 10 or m <= 3


def g13s_windows(date: datetime.date):
    winter     = is_winter(date)
    is_weekday = date.weekday() < 5

    # 🔋 Night charge window (tail-end top-up before morning peak)
    # Used to top up battery before 07:00 tariff increase / morning usage
    w1_start, w1_end = "06:30", "07:00"

    # 🌞 Midday charge window depends on season:
    # Winter: cheaper daytime block (limited solar production)
    # Summer: aligns with strong PV production + lowest risk of peak tariffs
    if winter:
        w2_start, w2_end = "13:30", "15:00"   # winter midday support window
    else:
        w2_start, w2_end = "15:30", "17:00"   # summer solar-aligned window

    # 🧠 Enable logic:
    # Window 1 (night): enabled on weekdays (main scheduled top-up)
    # Window 2 (midday): weekdays always enabled, weekends optional via config
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
        data = _post("/op/v0/device/real/query", {
            "sn": sn,
            "variables": ["SoC"]
        })

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
    """Return average cloud cover (%) over the next 3 hours from now.

    Uses timezone=auto so Open-Meteo returns times in local time, meaning
    the hour index directly corresponds to the local clock hour.
    Running every 15 min means we always want a near-future window, not a
    fixed daily slice.
    """
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude":     FORECAST_LAT,
            "longitude":    FORECAST_LON,
            "hourly":       "cloud_cover",
            "forecast_days": 1,
            "timezone":     "auto",          # local time index
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        # Find the index for the current local hour, then take next 3 hours
        current_hour = datetime.datetime.now().hour
        cloud = data["hourly"]["cloud_cover"][current_hour:current_hour + 3]
        if not cloud:
            return 0
        avg = sum(cloud) / len(cloud)
        print(f"  Cloud   : {avg:.0f}%  (hours {current_hour}–{current_hour + len(cloud) - 1} local, {len(cloud)}h avg)")
        return avg

    except Exception as e:
        print(f"Warning: cloud forecast failed ({e})")
        return 0  # assume good weather on failure

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
    soc = get_battery_soc(sn)
    if soc is None:
        print("SOC unknown → disabling SOC gating")
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

    # low_solar: >60% cloud cover generally overcast; in winter relax to >80%
    # because winter charging relies less on expected solar anyway
    low_solar = (cloud > 60) or (is_winter(today) and cloud > 80)

    if low_solar:
        min_soc_night = 35
        min_soc_day   = 60   # allow midday grid support
    else:
        min_soc_night = 25
        min_soc_day   = 35

    enable1 = enable1 and (soc < min_soc_night)
    enable2 = enable2 and (soc < min_soc_day)

    print(f"FoxESS Grid Charge Scheduler")
    print(f"  Device  : {sn}")
    print(f"  Today   : {today.strftime('%A, %d %b %Y')}")
    print(f"  Location: {FORECAST_LAT}, {FORECAST_LON}")
    print(f"  Mode    : {mode_label}")
    print(f"  SOC     : {soc:.1f}%")
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