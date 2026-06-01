"""
Charge state persistence for FoxESS Grid Charge Scheduler.

Stores the last window configuration we sent to FoxESS API so we can:
  1. Detect if a window is currently active without calling the API
  2. Skip all API calls when target=100% (FoxESS self-manages the limit)
  3. Cache the last successful solar radiation reading as fallback
  4. Count weather API failures for a daily Discord summary
"""
import datetime
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / ".charge_state"


# ── internal helpers ──────────────────────────────────────────────────────────

def _read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _write(data: dict):
    if data:
        STATE_FILE.write_text(json.dumps(data))
    elif STATE_FILE.exists():
        STATE_FILE.unlink()


# ── window config ─────────────────────────────────────────────────────────────

def save_windows(start1: str, end1: str, enabled1: bool,
                 start2: str, end2: str, enabled2: bool):
    """Save the window configuration we just sent to the API."""
    data = _read()
    data.update({
        "start1": start1, "end1": end1, "enabled1": enabled1,
        "start2": start2, "end2": end2, "enabled2": enabled2,
    })
    _write(data)


def get_last_windows() -> dict | None:
    """Return last saved window config or None."""
    data = _read()
    return data if "start1" in data else None


# ── skip state ────────────────────────────────────────────────────────────────

def save_skip(window_end: str):
    """Mark that we should skip API calls until window_end (target=100%).

    Stores a full ISO datetime so expiry works correctly across midnight —
    e.g. a Sunday 21:00 window does not stay active on Monday morning.
    """
    data = _read()
    now = datetime.datetime.now()
    h, m = map(int, window_end.split(":"))
    end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    data["skip_until"] = end_dt.isoformat()
    _write(data)


def clear_skip():
    """Clear target=100% skip state while keeping all other state."""
    data = _read()
    data.pop("skip_until", None)
    _write(data)


def should_skip(now: datetime.datetime) -> bool:
    """Return True if we should skip all API calls (target=100% active)."""
    try:
        data = _read()
        skip_until = data.get("skip_until")
        if not skip_until:
            return False
        end_dt = datetime.datetime.fromisoformat(skip_until)
        if now < end_dt:
            return True
        # Expired — remove skip flag but keep all other state
        data.pop("skip_until", None)
        _write(data)
        return False
    except Exception:
        return False


# ── solar radiation cache ─────────────────────────────────────────────────────

def save_radiation(radiation: float):
    """Persist the last successful solar forecast so we can fall back to it."""
    data = _read()
    data["last_radiation"] = radiation
    data["last_radiation_ts"] = datetime.datetime.now().isoformat()
    _write(data)


def get_last_radiation() -> float | None:
    """Return the last cached radiation value, or None if never fetched."""
    data = _read()
    return data.get("last_radiation")


def get_last_radiation_ts() -> datetime.datetime | None:
    """Return when the last radiation was fetched, or None."""
    data = _read()
    ts = data.get("last_radiation_ts")
    return datetime.datetime.fromisoformat(ts) if ts else None


# ── weather failure counter ───────────────────────────────────────────────────

def record_weather_failure():
    """Increment today's weather failure counter."""
    data = _read()
    today = datetime.date.today().isoformat()
    failures = data.get("weather_failures", {})
    # Reset counter if it's from a previous day
    if failures.get("date") != today:
        failures = {"date": today, "count": 0}
    failures["count"] += 1
    data["weather_failures"] = failures
    _write(data)


def get_weather_failures() -> tuple[int, str | None]:
    """Return (count, date) of recorded weather failures, or (0, None)."""
    data = _read()
    failures = data.get("weather_failures", {})
    return failures.get("count", 0), failures.get("date")


def clear_weather_failures():
    """Reset the weather failure counter after the daily report is sent."""
    data = _read()
    data.pop("weather_failures", None)
    _write(data)


# ── enabled-by-us markers ────────────────────────────────────────────────────

def mark_enabled(idx: int):
    """Record that we (the scheduler) enabled window idx."""
    data = _read()
    data[f"we_enabled{idx}"] = True
    _write(data)


def clear_enabled(idx: int):
    """Clear the enabled marker for window idx."""
    data = _read()
    data.pop(f"we_enabled{idx}", None)
    _write(data)


def was_enabled_by_us(idx: int) -> bool:
    """Return True if we enabled window idx in a previous run."""
    return bool(_read().get(f"we_enabled{idx}", False))


# ── full clear ────────────────────────────────────────────────────────────────

def clear():
    """Clear all state."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# ── backward compatibility ────────────────────────────────────────────────────

def save(window_end: str):
    save_skip(window_end)


def is_active(now: datetime.datetime) -> bool:
    return should_skip(now)
