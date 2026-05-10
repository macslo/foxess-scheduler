"""
Charge state persistence for FoxESS Grid Charge Scheduler.

When a charge window is activated, the state is saved to a file so
subsequent cron runs (every 2 min) can skip all API calls until the
window ends. FoxESS handles the actual charge limit internally.

This avoids:
- Unnecessary API calls while charging is in progress
- Oscillating notifications (100% → 99% → 100%) near full battery
"""
import datetime
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / ".charge_state"


def save(window_end: str):
    """Save active window state. window_end is HH:MM string."""
    STATE_FILE.write_text(json.dumps({"window_end": window_end}))


def clear():
    """Clear active window state."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def is_active(now: datetime.datetime) -> bool:
    """Return True if a window is currently active and not yet expired."""
    try:
        data = json.loads(STATE_FILE.read_text())
        h, m = map(int, data["window_end"].split(":"))
        end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < end_dt:
            return True
        # Window has ended — clean up
        clear()
        return False
    except Exception:
        return False
