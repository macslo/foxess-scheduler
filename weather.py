"""
Weather forecast for FoxESS Grid Charge Scheduler.
Uses Open-Meteo (free, no API key required).

Uses shortwave_radiation (W/m²) instead of cloud_cover — directly predicts
solar energy hitting the ground, much more accurate than cloud % for
determining whether panels will produce meaningful output.

Thresholds for your ~6kWp system:
  > 300 W/m²  → good solar, battery will recharge naturally
  150-300 W/m² → marginal, apply cloud bonus
  < 150 W/m²  → poor solar, need grid backup
"""
import datetime
import time
import requests
from notifier import notify_warning

# W/m² thresholds
SOLAR_GOOD = 300   # above this → low_solar = False
SOLAR_POOR = 150   # below this → low_solar = True (marginal between 150-300)


def _fetch_with_retry(url, params, retries=3):
    """GET request with exponential backoff on server errors."""
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)

            if r.status_code == 200:
                return r

            # retry on server errors (502/503/504)
            if 500 <= r.status_code < 600:
                print(f"[weather] HTTP {r.status_code}, retry {i+1}/{retries}")
                time.sleep(2 ** i)
                continue

            # non-retryable (400 etc.)
            print(f"[weather] HTTP {r.status_code} (not retrying)")
            return r

        except requests.RequestException as e:
            print(f"[weather] network error: {e}, retry {i+1}/{retries}")
            time.sleep(2 ** i)

    return None  # all retries exhausted


def get_solar_forecast(lat: float, lon: float) -> float:
    """Return average shortwave radiation (W/m²) relevant to the current decision.

    Before 09:00: sample 09:00-11:00 — first strong solar hours after morning peak.
    After 09:00:  sample next 3 hours from now — reflects current conditions
      for the evening window decision.

    Returns 999 (assume good solar) on forecast failure to avoid unnecessary charging.
    """
    try:
        r = _fetch_with_retry("https://api.open-meteo.com/v1/forecast", params={
            "latitude":      lat,
            "longitude":     lon,
            "hourly":        "shortwave_radiation",
            "forecast_days": 1,
            "timezone":      "auto",
        })

        if r is None:
            raise RuntimeError("All retries exhausted")

        r.raise_for_status()
        hourly = r.json()["hourly"]["shortwave_radiation"]

        current_hour = datetime.datetime.now().hour
        if current_hour < 9:
            radiation = hourly[9:11]
            label     = "solar hours 09:00–11:00"
        else:
            radiation = hourly[current_hour:current_hour + 3]
            label     = f"hours {current_hour}–{current_hour + len(radiation) - 1} local"

        if not radiation:
            return 999

        avg = sum(radiation) / len(radiation)
        quality = "☀️ good" if avg >= SOLAR_GOOD else ("⛅ marginal" if avg >= SOLAR_POOR else "☁️ poor")
        print(f"  Solar   : {avg:.0f} W/m²  ({label}, {len(radiation)}h avg)  {quality}")
        return avg

    except Exception as e:
        notify_warning(f"Solar forecast failed: {e}")
        print(f"Warning: solar forecast failed ({e})")
        return 999  # assume good solar on failure — avoid unnecessary grid charging


def is_low_solar(radiation: float, winter: bool) -> bool:
    """Return True when solar forecast suggests poor panel output.

    Winter threshold is lower — panels produce less even on clear days
    due to low sun angle, so we're more conservative about triggering bonus.
    """
    if winter:
        return radiation < SOLAR_POOR        # only truly poor days get bonus
    else:
        return radiation < SOLAR_GOOD        # marginal days (150-300) also get bonus
