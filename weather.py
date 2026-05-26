"""
Weather forecast for FoxESS Grid Charge Scheduler.
Uses Open-Meteo (free, no API key required).

Uses shortwave_radiation (W/m²) instead of cloud_cover — directly predicts
solar energy hitting the ground, much more accurate than cloud % for
determining whether panels will produce meaningful output.

Thresholds for ~6kWp system:
  > 300 W/m²   → good solar, battery will recharge naturally
  150-300 W/m² → marginal, apply cloud bonus
  < 150 W/m²   → poor solar, need grid backup

Fallback on API failure:
  - Use last cached radiation from charge_state if available
  - Otherwise assume SOLAR_GOOD (sunny) — avoids unnecessary grid charging
  - Failures are counted and reported once per day via Discord
"""
import datetime
import time
import random
import requests
import charge_state

SOLAR_GOOD = 300
SOLAR_POOR = 150


def _fetch_with_retry(url, params, retries=5):
    """GET with exponential backoff + random jitter on server errors.

    Jitter avoids thundering herd when many schedulers fire at the same time
    (e.g. around full hours). Backoff: 2^i + random(0-2) seconds per retry.
    """
    # Initial jitter — spread requests that fire at the same cron second
    time.sleep(random.uniform(0, 3))

    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=(3, 10))
            if r.status_code == 200:
                return r
            if 500 <= r.status_code < 600 or r.status_code == 429:
                wait = (2 ** i) + random.uniform(0, 2)
                print(f"[weather] HTTP {r.status_code}, retry {i+1}/{retries} in {wait:.1f}s")
                time.sleep(wait)
                continue
            print(f"[weather] HTTP {r.status_code} (not retrying)")
            return r
        except requests.RequestException as e:
            wait = (2 ** i) + random.uniform(0, 2)
            print(f"[weather] network error: {e}, retry {i+1}/{retries} in {wait:.1f}s")
            time.sleep(wait)
    return None


def _fallback_radiation() -> float:
    """Return cached radiation or SOLAR_GOOD, and log appropriately."""
    cached = charge_state.get_last_radiation()
    if cached is not None:
        ts = charge_state.get_last_radiation_ts()
        age = ""
        if ts:
            mins = int((datetime.datetime.now() - ts).total_seconds() / 60)
            age = f", {mins}min ago"
        print(f"  Solar   : using cached {cached:.0f} W/m²{age} (API unavailable)")
        return cached
    print(f"  Solar   : API unavailable, no cache — assuming sunny ({SOLAR_GOOD} W/m²)")
    return float(SOLAR_GOOD)


def get_solar_forecast(lat: float, lon: float) -> float:
    """Return average shortwave radiation (W/m²) relevant to the current decision.

    Before 09:00: sample 09:00-11:00 — first strong solar hours after morning peak.
    09:00-18:00:  sample next 3 hours from now — reflects current conditions.
    After 18:00:  return SOLAR_GOOD — panels no longer producing regardless of
                  forecast, no point fetching or adding cloud bonus to targets.

    On failure: returns last cached value, or SOLAR_GOOD if no cache exists.
    Failures are silently counted; a daily summary is sent to Discord.
    """
    current_hour = datetime.datetime.now().hour

    if current_hour >= 18:
        print(f"  Solar   : skipped (after 18:00 — panels not producing)")
        return SOLAR_GOOD

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

        if current_hour < 9:
            radiation = hourly[9:11]
            label     = "solar hours 09:00–11:00"
        else:
            radiation = hourly[current_hour:current_hour + 3]
            label     = f"hours {current_hour}–{current_hour + len(radiation) - 1} local"

        if not radiation:
            return float(SOLAR_GOOD)

        avg     = sum(radiation) / len(radiation)
        quality = "☀️ good" if avg >= SOLAR_GOOD else ("⛅ marginal" if avg >= SOLAR_POOR else "☁️ poor")
        print(f"  Solar   : {avg:.0f} W/m²  ({label}, {len(radiation)}h avg)  {quality}")

        # Persist for fallback use on future failures
        charge_state.save_radiation(avg)

        # If there were earlier failures today, send one summary now and reset
        fail_count, fail_date = charge_state.get_weather_failures()
        if fail_count > 0:
            from notifier import notify_weather_failures
            notify_weather_failures(fail_count, fail_date, avg)
            charge_state.clear_weather_failures()

        return avg

    except Exception as e:
        print(f"Warning: solar forecast failed ({e})")
        charge_state.record_weather_failure()
        return _fallback_radiation()


def is_low_solar(radiation: float, winter: bool) -> bool:
    """Return True when solar forecast suggests poor panel output.

    Winter: only truly poor days trigger bonus — solar is weak regardless.
    Summer: marginal days (150-300) also trigger bonus.
    """
    if winter:
        return radiation < SOLAR_POOR
    else:
        return radiation < SOLAR_GOOD
