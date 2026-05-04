"""
Weather forecast for FoxESS Grid Charge Scheduler.
Uses Open-Meteo (free, no API key required).
"""
import datetime
import requests
from notifier import notify_warning


def get_cloud_forecast(lat: float, lon: float) -> float:
    """Return average cloud cover (%) relevant to the current decision.

    Before 09:00: sample 09:00-11:00 — first solar hours after morning peak.
      If cloudy then, battery won't recharge quickly after 07:00-09:00 draw.
    After 09:00: sample next 3 hours from now — reflects current conditions
      for the evening window decision.

    Returns 0 (assume clear) on forecast failure.
    """
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude":      lat,
            "longitude":     lon,
            "hourly":        "cloud_cover",
            "forecast_days": 1,
            "timezone":      "auto",
        }, timeout=10)
        r.raise_for_status()
        hourly = r.json()["hourly"]["cloud_cover"]

        current_hour = datetime.datetime.now().hour
        if current_hour < 9:
            cloud = hourly[9:11]
            label = "solar hours 09:00–11:00"
        else:
            cloud = hourly[current_hour:current_hour + 3]
            label = f"hours {current_hour}–{current_hour + len(cloud) - 1} local"

        if not cloud:
            return 0

        avg = sum(cloud) / len(cloud)
        print(f"  Cloud   : {avg:.0f}%  ({label}, {len(cloud)}h avg)")
        return avg

    except Exception as e:
        notify_warning(f"Cloud forecast failed: {e}")
        print(f"Warning: cloud forecast failed ({e})")
        return 0


def is_low_solar(cloud: float, winter: bool) -> bool:
    """Return True when cloud cover suggests poor solar production.

    Winter threshold is higher — short days mean less solar contribution
    regardless, so we only apply the bonus on heavily overcast days.
    """
    return cloud > 60 or (winter and cloud > 80)
