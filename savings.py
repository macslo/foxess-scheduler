"""
Savings tracker for FoxESS Grid Charge Scheduler.

Records each grid charge session and estimates cost savings vs paying
peak rate. Uses SQLite for queryable history.

Savings formula:
  duration_min  = end_time - start_time
  kwh_charged   = duration_min / 60 × BATTERY_CHARGE_RATE_KW
  saved_pln     = kwh_charged × (peak_rate - charge_rate)

where charge_rate is the cheap/midday rate during the window,
and peak_rate is what we would have paid without the window.

CLI usage (called from foxess_grid_charge_scheduler.py --savings):
  --savings 7d       last 7 days
  --savings 30d      last 30 days
  --savings 2026-05  calendar month
  --savings all      everything
"""
import datetime
import sqlite3
from pathlib import Path
from typing import NamedTuple

import config as cfg

DB_FILE = Path(__file__).parent / ".savings.db"

# ── Tariff prices (zł/kWh) ────────────────────────────────────────────────────
# Source: config.py comments (Tauron G13s 12/2025)
# These are the rates we charge at (cheap window) vs what we avoid (peak).
# Overridable via env vars for future tariff changes.

import os

# ── Tauron G13s prices (zł/kWh, total = sales + distribution) ─────────────────
# Source: README / tanie_godziny_jak_dziala_taryfa_g13s_12_2025.pdf
# All overridable via FOXESS_PRICE_* env vars for future tariff changes.

# Winter weekday
PRICE_WINTER_WD_NIGHT    = float(os.getenv("FOXESS_PRICE_WINTER_WD_NIGHT",    "0.7435"))  # 21:00-07:00
PRICE_WINTER_WD_MIDDAY   = float(os.getenv("FOXESS_PRICE_WINTER_WD_MIDDAY",   "0.9286"))  # 10:00-15:00 ← charge here
PRICE_WINTER_WD_PEAK     = float(os.getenv("FOXESS_PRICE_WINTER_WD_PEAK",     "1.2821"))  # 07:00-10:00, 15:00-21:00 ← avoid

# Winter weekend
PRICE_WINTER_WE_NIGHT    = float(os.getenv("FOXESS_PRICE_WINTER_WE_NIGHT",    "0.7435"))  # 21:00-07:00
PRICE_WINTER_WE_CHEAPEST = float(os.getenv("FOXESS_PRICE_WINTER_WE_CHEAPEST", "0.5597"))  # 10:00-15:00 ← charge here
PRICE_WINTER_WE_NEUTRAL  = float(os.getenv("FOXESS_PRICE_WINTER_WE_NEUTRAL",  "0.7669"))  # 07:00-10:00, 15:00-21:00

# Summer weekday
PRICE_SUMMER_WD_NIGHT    = float(os.getenv("FOXESS_PRICE_SUMMER_WD_NIGHT",    "0.7558"))  # 21:00-07:00
PRICE_SUMMER_WD_CHEAP    = float(os.getenv("FOXESS_PRICE_SUMMER_WD_CHEAP",    "0.4613"))  # 09:00-17:00 ← charge here
PRICE_SUMMER_WD_PEAK     = float(os.getenv("FOXESS_PRICE_SUMMER_WD_PEAK",     "1.2219"))  # 07:00-09:00, 17:00-21:00 ← avoid

# Summer weekend
PRICE_SUMMER_WE_NIGHT    = float(os.getenv("FOXESS_PRICE_SUMMER_WE_NIGHT",    "0.7558"))  # 21:00-07:00
PRICE_SUMMER_WE_CHEAPEST = float(os.getenv("FOXESS_PRICE_SUMMER_WE_CHEAPEST", "0.1882"))  # 09:00-17:00 ← very cheap
PRICE_SUMMER_WE_NEUTRAL  = float(os.getenv("FOXESS_PRICE_SUMMER_WE_NEUTRAL",  "0.4972"))  # 07:00-09:00, 17:00-21:00


class Session(NamedTuple):
    ts:           str    # ISO datetime of INSERT (when scheduler ran)
    window:       int    # 1 or 2
    start_time:   str    # "HH:MM"
    end_time:     str    # "HH:MM"
    duration_min: int
    kwh:          float
    charge_rate:  float  # zł/kWh — rate we paid
    peak_rate:    float  # zł/kWh — rate we avoided
    saved_pln:    float
    soc_start:    float | None
    strategy:     str
    session_date: str    # YYYY-MM-DD — date the window was active (not INSERT date)


# ── DB setup ──────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    # Migration: add session_date column if upgrading from older schema
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_date TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            window       INTEGER NOT NULL,
            start_time   TEXT NOT NULL,
            end_time     TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            kwh          REAL NOT NULL,
            charge_rate  REAL NOT NULL,
            peak_rate    REAL NOT NULL,
            saved_pln    REAL NOT NULL,
            soc_start    REAL,
            strategy     TEXT NOT NULL DEFAULT '',
            session_date TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.commit()
    return conn


# ── Rate lookup ───────────────────────────────────────────────────────────────

def _rates(winter: bool, weekend: bool, start_time: str) -> tuple[float, float]:
    """Return (charge_rate, peak_rate) for the given window slot.

    charge_rate: what we pay during the cheap charging window
    peak_rate:   what we would have paid without the window (avoided cost)

    Window 1 (≈06:30-07:00): charges just before morning peak → avoids peak
    Window 2 (≈13:00-17:00): charges during cheap midday → avoids evening peak
    Sunday evening (20:00-21:00): charges at night rate → avoids Monday peak
    """
    h = int(start_time.split(":")[0])

    # Sunday evening window — charges during night rate, avoids Monday morning peak
    if h >= 19:
        if winter:
            return PRICE_WINTER_WD_NIGHT, PRICE_WINTER_WD_PEAK
        return PRICE_SUMMER_WD_NIGHT, PRICE_SUMMER_WD_PEAK

    if winter:
        if weekend:
            # Weekend: charge during cheapest midday, no true peak to avoid
            # saving = cheapest vs neutral (what we'd pay at 15:00-21:00)
            return PRICE_WINTER_WE_CHEAPEST, PRICE_WINTER_WE_NEUTRAL
        else:
            # Weekday: charge during midday cheap, avoid peak
            return PRICE_WINTER_WD_MIDDAY, PRICE_WINTER_WD_PEAK
    else:
        if weekend:
            # Weekend: charge during very cheap solar hours, no true peak
            return PRICE_SUMMER_WE_CHEAPEST, PRICE_SUMMER_WE_NEUTRAL
        else:
            # Weekday: charge during cheap solar hours, avoid peak
            return PRICE_SUMMER_WD_CHEAP, PRICE_SUMMER_WD_PEAK


# ── Public API ────────────────────────────────────────────────────────────────

def record_session(
    window: int,
    start_time: str,
    end_time: str,
    soc_start: float | None,
    winter: bool,
    strategy_name: str,
    weekend: bool = False,
    session_date: str | None = None,
):
    """Record a completed charge session. Called when a window is disabled
    after having been enabled — duration = clock time between start and end.

    If end_time <= start_time (shouldn't happen in practice), session is skipped.
    """
    h_s, m_s = map(int, start_time.split(":"))
    h_e, m_e = map(int, end_time.split(":"))
    duration_min = (h_e * 60 + m_e) - (h_s * 60 + m_s)
    if duration_min <= 0:
        return

    kwh          = round(duration_min / 60 * cfg.BATTERY_CHARGE_RATE_KW, 3)
    charge_rate, peak_rate = _rates(winter, weekend, start_time)
    saved_pln    = round(kwh * (peak_rate - charge_rate), 2)
    date         = session_date or datetime.date.today().isoformat()

    conn = _connect()
    conn.execute(
        """INSERT INTO sessions
           (ts, window, start_time, end_time, duration_min, kwh,
            charge_rate, peak_rate, saved_pln, soc_start, strategy, session_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.datetime.now().isoformat(), window, start_time, end_time,
         duration_min, kwh, charge_rate, peak_rate, saved_pln, soc_start,
         strategy_name, date),
    )
    conn.commit()
    conn.close()


def query_savings(period: str) -> list[Session]:
    """Return sessions matching period string.

    period examples: '7d', '30d', '2026-05', 'all'
    """
    conn = _connect()
    now  = datetime.datetime.now()

    if period == "all":
        rows = conn.execute("SELECT * FROM sessions ORDER BY session_date, start_time").fetchall()
    elif period.endswith("d") and period[:-1].isdigit():
        days  = int(period[:-1])
        since = (now - datetime.timedelta(days=days)).isoformat()
        rows  = conn.execute(
            "SELECT * FROM sessions WHERE ts >= ? ORDER BY session_date, start_time", (since,)
        ).fetchall()
    elif len(period) == 7 and period[4] == "-":
        # YYYY-MM
        rows = conn.execute(
            "SELECT * FROM sessions WHERE ts LIKE ? ORDER BY session_date, start_time",
            (f"{period}%",)
        ).fetchall()
    else:
        print(f"[savings] Unknown period '{period}'. Use: 7d, 30d, 2026-05, all")
        conn.close()
        return []

    conn.close()
    return [Session(*row[1:]) for row in rows]  # skip auto-id


def savings_summary(period: str) -> dict:
    """Return aggregated summary dict for the period."""
    sessions = query_savings(period)
    if not sessions:
        return {"period": period, "sessions": 0, "kwh": 0.0, "saved_pln": 0.0}
    return {
        "period":     period,
        "sessions":   len(sessions),
        "kwh":        round(sum(s.kwh for s in sessions), 2),
        "saved_pln":  round(sum(s.saved_pln for s in sessions), 2),
        "sessions_detail": sessions,
    }


# ── CLI report ────────────────────────────────────────────────────────────────

def print_report(period: str):
    """Print savings report to stdout."""
    summary = savings_summary(period)
    if summary["sessions"] == 0:
        print(f"[savings] No sessions recorded for period: {period}")
        return

    print(f"\n{'═' * 52}")
    print(f"  Savings report — {period}")
    print(f"{'═' * 52}")
    print(f"  Sessions  : {summary['sessions']}")
    print(f"  kWh       : {summary['kwh']:.2f} kWh")
    print(f"  Saved     : {summary['saved_pln']:.2f} zł")
    print(f"{'─' * 52}")

    for s in summary["sessions_detail"]:
        date = s.session_date or s.ts[:10]
        soc  = f"  SOC={s.soc_start:.0f}%" if s.soc_start is not None else ""
        print(f"  {date}  w{s.window}  {s.start_time}–{s.end_time}"
              f"  {s.duration_min}min  {s.kwh:.2f}kWh"
              f"  {s.saved_pln:.2f}zł{soc}")
    print(f"{'═' * 52}\n")


def discord_report(period: str) -> dict | None:
    """Return a Discord embed dict for the savings report, or None if no data."""
    from notifier import _embed, COLOR_GREEN
    summary = savings_summary(period)
    if summary["sessions"] == 0:
        return None

    sessions = summary["sessions_detail"]
    # Build per-session lines (last 10 to avoid hitting Discord limits)
    lines = []
    for s in sessions[-10:]:
        date = s.session_date or s.ts[:10]
        lines.append(f"`{date}` w{s.window} {s.start_time}–{s.end_time} "
                     f"{s.duration_min}min · **{s.saved_pln:.2f} zł**")
    if len(sessions) > 10:
        lines.insert(0, f"_(showing last 10 of {len(sessions)} sessions)_")

    return _embed(
        title       = f"💰 Savings report — {period}",
        description = "\n".join(lines),
        color       = COLOR_GREEN,
        fields      = [
            {"name": "Sessions",  "value": str(summary["sessions"]),        "inline": True},
            {"name": "kWh",       "value": f"{summary['kwh']:.2f} kWh",     "inline": True},
            {"name": "Saved",     "value": f"{summary['saved_pln']:.2f} zł","inline": True},
        ],
    )
