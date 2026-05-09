"""
Window timing helpers for FoxESS Grid Charge Scheduler.

Handles proximity detection, freeze logic and status reporting
for the two FoxESS force-charge windows.
"""
import datetime
import config as cfg


def minutes_until(now: datetime.datetime, window_start: str) -> int:
    """Return minutes from now until window_start. Negative if already past."""
    h, m = map(int, window_start.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return int((target - now).total_seconds() / 60)


def is_closed(now: datetime.datetime, end_time: str) -> bool:
    """Return True if the window end time has already passed."""
    h, m = map(int, end_time.split(":"))
    return now >= now.replace(hour=h, minute=m, second=0, microsecond=0)


def is_not_opened_yet(now: datetime.datetime, start_time: str) -> bool:
    """Return True if window start is more than WINDOW_LEAD_MINUTES away."""
    return minutes_until(now, start_time) > cfg.WINDOW_LEAD_MINUTES


def near_window(now: datetime.datetime, strategy, ctx) -> bool:
    """Return True if now is within WINDOW_LEAD_MINUTES before either window
    start, or inside the window itself.

    ctx: ChargeContext passed to strategy.get_window1/get_window2.
    """
    lead = cfg.WINDOW_LEAD_MINUTES
    for start, end in (strategy.get_window1(ctx), strategy.get_window2(ctx)):
        mins   = minutes_until(now, start)
        h, m   = map(int, end.split(":"))
        end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if 0 <= mins <= lead:
            return True
        if mins <= 0 and now <= end_dt:
            return True
    return False


def window_status(now: datetime.datetime, enable, start: str, end: str) -> str:
    """Human-readable status for a window — used in log output."""
    if is_closed(now, end):
        return "FROZEN (window closed)"
    if is_not_opened_yet(now, start):
        return "FROZEN (not opened yet)"
    return "ENABLE" if enable else "DISABLE"
