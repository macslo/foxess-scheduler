"""
Charge state persistence for FoxESS Grid Charge Scheduler.

Only used for target=100% — when FoxESS manages the charge limit itself
and we can skip all API calls until the window ends.

Window time locking (to prevent dynamic recalculation causing spurious
notifications) is handled directly in _apply() using API times as source
of truth — no file needed for that.
"""
import datetime
import json
from pathlib import Path

STATE_FILE = Path(__file__).parent / ".charge_state"


def save(window_end: str):
    """Save active window end time. Only called when target=100%."""
    STATE_FILE.write_text(json.dumps({"window_end": window_end}))


def clear():
    """Clear active window state."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def should_skip(now: datetime.datetime) -> bool:
    """Return True if run should be skipped entirely (target=100%, FoxESS self-managing)."""
    try:
        data = json.loads(STATE_FILE.read_text())
        h, m = map(int, data["window_end"].split(":"))
        end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < end_dt:
            return True
        clear()
        return False
    except Exception:
        return False


# Keep is_active as alias for backward compatibility
def is_active(now: datetime.datetime) -> bool:
    return should_skip(now)
