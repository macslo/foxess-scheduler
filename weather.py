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
import requests
from notifier import notify_warning

# W/m² thresholds
SOLAR_GOOD = 300   # above this → low_solar = False
SOLAR_POOR = 150   # below this → low_solar = True (marginal between 150-300)


def get_solar_forecast(lat: float, lon: float) -> float:
    """Return average shortwave radiation (W/m²) relevant to the current decision.

    Before 09:00: sample 09:00-11:00 — first strong solar hours after morning peak.
    After 09:00:  sample next 3 hours from now — reflects current conditions
      for the evening window decision.

    Returns 999 (assume good solar) on forecast failure to avoid unnecessary charging.
    """
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude":      lat,
            "longitude":     lon,
            "hourly":        "shortwave_radiation",
            "forecast_days": 1,
            "timezone":      "auto",
        }, timeout=10)
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
