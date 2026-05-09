"""
FoxESS Cloud API client for FoxESS Grid Charge Scheduler.

Handles authentication, device discovery, SOC reading
and charge window get/set.
"""
import hashlib
import time
import requests
from notifier import notify_warning

BASE_URL = "https://www.foxesscloud.com"

# Set by main script after loading .env
API_KEY = ""


def _headers(path: str) -> dict:
    ts  = str(round(time.time() * 1000))
    sig = hashlib.md5(rf"{path}\r\n{API_KEY}\r\n{ts}".encode()).hexdigest()
    return {
        "token":        API_KEY,
        "timestamp":    ts,
        "signature":    sig,
        "lang":         "en",
        "Content-Type": "application/json",
        "User-Agent":   "Mozilla/5.0 foxess-scheduler/1.0",
    }


def _get(path: str, params=None) -> dict:
    r = requests.get(BASE_URL + path, headers=_headers(path), params=params, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("errno", 0) != 0:
        raise RuntimeError(f"API [{d.get('errno')}]: {d.get('msg', d)}")
    return d


def _post(path: str, body: dict) -> dict:
    r = requests.post(BASE_URL + path, headers=_headers(path), json=body, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("errno", 0) != 0:
        raise RuntimeError(f"API [{d.get('errno')}]: {d.get('msg', d)}")
    return d


def get_first_sn() -> str:
    """Auto-detect the first device serial number on the account."""
    data    = _post("/op/v0/device/list", {"currentPage": 1, "pageSize": 10})
    devices = data.get("result", {}).get("data", [])
    if not devices:
        raise RuntimeError("No devices found on this account.")
    sn = devices[0]["deviceSN"]
    print(f"  Auto-detected SN: {sn}")
    if len(devices) > 1:
        print(f"  ({len(devices)} devices found -- using first. Set FOXESS_SN to choose.)")
    return sn


def get_charge_settings(sn: str) -> dict:
    """Return current force-charge window settings for the device."""
    return _get("/op/v0/device/battery/forceChargeTime/get", {"sn": sn}).get("result", {})


def set_charge_windows(
    sn: str,
    enable1: bool, start1: str, end1: str,
    enable2: bool, start2: str, end2: str,
) -> dict:
    """Apply force-charge window settings to the device."""
    def t(s):
        h, m = map(int, s.split(":"))
        return {"hour": h, "minute": m}

    return _post("/op/v0/device/battery/forceChargeTime/set", {
        "sn":         sn,
        "enable1":    enable1, "startTime1": t(start1), "endTime1": t(end1),
        "enable2":    enable2, "startTime2": t(start2), "endTime2": t(end2),
    })


def get_battery_soc(sn: str) -> float | None:
    """Return current battery SOC%, or None on failure."""
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


def get_device_data(sn: str) -> tuple[float | None, float | None]:
    """Return (soc%, pv_kw) in a single API call, or (None, None) on failure.

    PV variable assumed to be 'pvPower' — verify against actual API response
    and update if needed (e.g. 'generationPower').
    """
    try:
        data   = _post("/op/v0/device/real/query", {"sn": sn, "variables": ["SoC", "pvPower"]})
        result = data.get("result", [])
        if not result:
            return None, None
        datas  = result[0].get("datas", [])
        values = {d["variable"]: float(d.get("value", 0)) for d in datas if "variable" in d}
        soc    = values.get("SoC")
        pv_kw  = values.get("pvPower")
        print(f"  SOC     : {soc:.1f}%  PV: {pv_kw:.2f} kW")
        return soc, pv_kw
    except Exception as e:
        notify_warning(f"Failed to read device data: {e}")
        print(f"Warning: failed to read device data ({e})")
    return None, None
