"""
Charge state persistence for FoxESS Grid Charge Scheduler.

Stores the last window configuration we sent to FoxESS API so we can:
  1. Detect if a window is currently active without calling the API
  2. Skip all API calls when target=100% (FoxESS self-manages the limit)
"""
import datetime
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / ".charge_state"


def save_windows(start1: str, end1: str, enabled1: bool,
                 start2: str, end2: str, enabled2: bool):
    """Save the window configuration we just sent to the API."""
    data = {}
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    data.update({
        "start1": start1, "end1": end1, "enabled1": enabled1,
        "start2": start2, "end2": end2, "enabled2": enabled2,
    })
    STATE_FILE.write_text(json.dumps(data))


def save_skip(window_end: str):
    """Mark that we should skip API calls until window_end (target=100%).

    Stores a full ISO datetime so expiry works correctly across midnight —
    e.g. a Sunday 21:00 window does not stay active on Monday morning.
    """
    data = {}
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    now = datetime.datetime.now()
    h, m = map(int, window_end.split(":"))
    end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    data["skip_until"] = end_dt.isoformat()
    STATE_FILE.write_text(json.dumps(data))


def clear_skip():
    """Clear target=100% skip state while keeping last window config."""
    try:
        data = json.loads(STATE_FILE.read_text())
    except Exception:
        return
    data.pop("skip_until", None)
    if data:
        STATE_FILE.write_text(json.dumps(data))
    elif STATE_FILE.exists():
        STATE_FILE.unlink()


def clear():
    """Clear all state."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def get_last_windows() -> dict | None:
    """Return last saved window config or None."""
    try:
        data = json.loads(STATE_FILE.read_text())
        if "start1" in data:
            return data
    except Exception:
        pass
    return None


def should_skip(now: datetime.datetime) -> bool:
    """Return True if we should skip all API calls (target=100% active)."""
    try:
        data = json.loads(STATE_FILE.read_text())
        skip_until = data.get("skip_until")
        if not skip_until:
            return False
        end_dt = datetime.datetime.fromisoformat(skip_until)
        if now < end_dt:
            return True
        # Expired — remove skip flag but keep window config
        data.pop("skip_until", None)
        if data:
            STATE_FILE.write_text(json.dumps(data))
        elif STATE_FILE.exists():
            STATE_FILE.unlink()
        return False
    except Exception:
        return False


# Backward compatibility
def save(window_end: str):
    save_skip(window_end)


def is_active(now: datetime.datetime) -> bool:
    return should_skip(now)
