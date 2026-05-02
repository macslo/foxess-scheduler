# FoxESS Grid Charge Scheduler

A Python script that automatically manages battery grid charging windows on a FoxESS inverter, optimised for the **Tauron G13s time-of-use tariff** (Poland). Runs as a scheduled task (e.g. cron on QNAP NAS) every 15 minutes.

## How it works

The script decides whether to **enable or disable** each of the two FoxESS force-charge windows based on:

- **Current battery SOC** — no point charging if the battery is already full
- **Season** (summer/winter) and **day type** (weekday/weekend) — different peak hours apply
- **Cloud cover forecast** — if solar production will be poor, charge targets are raised to compensate
- **Tariff-aware targets** — SOC thresholds are calculated to cover the next expensive peak block without drawing from the grid

### Tauron G13s tariff schedule

**Winter (1 Oct – 31 Mar) — Weekdays**
| Time | Rate | Sales | Distribution | **Total** |
|------|------|-------|-------------|-----------|
| 21:00 – 07:00 | 🟡 Cheap night | 0.6089 | 0.1346 | **0.7435 zł/kWh** |
| 10:00 – 15:00 | 🟡 Cheap midday | 0.6827 | 0.2459 | **0.9286 zł/kWh** |
| 07:00–10:00 and 15:00–21:00 | 🔴 **Peak — do not charge** | 0.8723 | 0.4098 | **1.2821 zł/kWh** |

**Winter (1 Oct – 31 Mar) — Weekends**
| Time | Rate | Sales | Distribution | **Total** |
|------|------|-------|-------------|-----------|
| 21:00 – 07:00 | 🟡 Cheap night | 0.6089 | 0.1346 | **0.7435 zł/kWh** |
| 10:00 – 15:00 | 🟢 Cheapest midday | 0.4121 | 0.1476 | **0.5597 zł/kWh** |
| 07:00–10:00 and 15:00–21:00 | 🟡 Neutral — no true peak | 0.5258 | 0.2411 | **0.7669 zł/kWh** |

**Summer (1 Apr – 30 Sep) — Weekdays**
| Time | Rate | Sales | Distribution | **Total** |
|------|------|-------|-------------|-----------|
| 21:00 – 07:00 | 🟡 Cheap night | 0.6212 | 0.1346 | **0.7558 zł/kWh** |
| 09:00 – 17:00 | 🟢 Cheapest (solar hours) | 0.3383 | 0.1230 | **0.4613 zł/kWh** |
| 07:00–09:00 and 17:00–21:00 | 🔴 **Peak — do not charge** | 0.8723 | 0.3496 | **1.2219 zł/kWh** |

**Summer (1 Apr – 30 Sep) — Weekends**
| Time | Rate | Sales | Distribution | **Total** |
|------|------|-------|-------------|-----------|
| 21:00 – 07:00 | 🟡 Cheap night | 0.6212 | 0.1346 | **0.7558 zł/kWh** |
| 09:00 – 17:00 | 🟢 Very cheap | 0.1390 | 0.0492 | **0.1882 zł/kWh** |
| 07:00–09:00 and 17:00–21:00 | 🟡 Neutral — no true peak | 0.3526 | 0.1446 | **0.4972 zł/kWh** |

> Prices from official Tauron PDF (12/2025). Peak hours on weekdays are roughly **3× more expensive** than cheap midday — well worth avoiding. Weekends have no true peak in either season. For current prices see the [Tauron G13s tariff page](https://www.tauron.pl/dla-domu/prad/prad-z-usluga/tanie-godziny).

### Charge windows

| Window | Time | Purpose |
|--------|------|---------|
| Window 1 | 06:30 – 07:00 | Morning top-up before peak |
| Window 2 | 15:30 – 17:00 (summer) / 13:30 – 15:00 (winter) | Pre-evening top-up before peak |

---

## Requirements

- Python 3.7+
- `requests` library (`pip install requests`)
- FoxESS Cloud API key ([foxesscloud.com](https://www.foxesscloud.com) → Avatar → Personal Centre → API Management)

---

## Installation

```bash
git clone https://github.com/macslo/foxess-scheduler.git
cd foxess-scheduler
pip install requests
cp .env.example .env
# edit .env with your API key and settings
```

---

## Configuration

All settings are controlled via a `.env` file in the same directory as the script. Create it based on the example below:

```ini
# ── Required ──────────────────────────────────────────────────────────────────
FOXESS_API_KEY=your_api_key_here

# ── Device (leave blank or set to "auto" to detect automatically) ─────────────
FOXESS_SN=auto

# ── Tariff mode ───────────────────────────────────────────────────────────────
# g13s   = automatic Tauron G13s seasonal schedule (default)
# manual = use your own windows defined below
FOXESS_TARIFF=g13s

# ── Location for cloud cover forecast ────────────────────────────────────────
FOXESS_LAT=50.2849
FOXESS_LON=18.6717

# ── SOC charge targets (%) ───────────────────────────────────────────────────
# Minimum SOC to reach before each peak block.
# Defaults calculated for 9.4 kWh battery, ~2000W peak draw, 10% discharge floor.
FOXESS_TARGET_SUMMER_WEEKDAY_MORNING=50
FOXESS_TARGET_SUMMER_WEEKDAY_EVENING=55
FOXESS_TARGET_SUMMER_WEEKEND_MORNING=30
FOXESS_TARGET_SUMMER_WEEKEND_EVENING=40
FOXESS_TARGET_WINTER_WEEKDAY_MORNING=65
FOXESS_TARGET_WINTER_WEEKDAY_EVENING=95
FOXESS_TARGET_WINTER_WEEKEND_MORNING=65
FOXESS_TARGET_WINTER_WEEKEND_EVENING=95

# Extra % added to targets when cloud cover > 60% (poor solar expected)
FOXESS_CLOUD_BONUS=20

# ── G13s options ──────────────────────────────────────────────────────────────
# Enable midday charge window on weekends (whole day is cheap anyway)
FOXESS_G13S_WEEKEND_MIDDAY=false

# ── Manual windows (only used when FOXESS_TARIFF=manual) ─────────────────────
FOXESS_CHARGE1_START=01:00
FOXESS_CHARGE1_END=05:00
FOXESS_CHARGE1_ENABLE=weekdays    # always / never / weekdays / weekends
FOXESS_CHARGE2_START=13:00
FOXESS_CHARGE2_END=15:00
FOXESS_CHARGE2_ENABLE=never
```

---

## Running on QNAP NAS

### Manual run

```bash
/share/CACHEDEV1_DATA/.qpkg/Python3/python3/bin/python3 /path/to/foxess_grid_charge_scheduler.py
```

### Cron schedule (Task Scheduler or crontab)

```
# Every 15 minutes
*/15 * * * * /share/CACHEDEV1_DATA/.qpkg/Python3/python3/bin/python3 /share/CACHEDEV1_DATA/homes/madmin/share/foxcloud/foxess_grid_charge_scheduler.py >> /share/CACHEDEV1_DATA/homes/madmin/foxess.log 2>&1

# Extra trigger at 06:29 on weekdays to ensure full morning window is used
29 6 * * 1-5 /share/CACHEDEV1_DATA/.qpkg/Python3/python3/bin/python3 /share/CACHEDEV1_DATA/homes/madmin/share/foxcloud/foxess_grid_charge_scheduler.py >> /share/CACHEDEV1_DATA/homes/madmin/foxess.log 2>&1
```

### Updating from GitHub

```bash
curl -fsSL https://raw.githubusercontent.com/macslo/foxess-scheduler/refs/heads/main/foxess_grid_charge_scheduler.py \
  -o /share/CACHEDEV1_DATA/homes/madmin/share/foxcloud/foxess_grid_charge_scheduler.py
```

Add as an alias in `~/.profile` for convenience:
```bash
alias update-foxess="curl -fsSL https://raw.githubusercontent.com/macslo/foxess-scheduler/refs/heads/main/foxess_grid_charge_scheduler.py -o /share/CACHEDEV1_DATA/homes/madmin/share/foxcloud/foxess_grid_charge_scheduler.py"
```

---

## Example output

```
[RUN] 2026-05-02T15:34:53.697804
  Cloud   : 12%  (hours 15–17 local, 3h avg)
FoxESS Grid Charge Scheduler
  Device  : XYZ
  Today   : Saturday, 02 May 2026
  Location: 50.2849, 18.6717
  Mode    : Tauron G13s  season=SUMMER  day=weekend
  SOC     : 99.0%  (morning target=30%  evening target=40%)
  Window 1: 06:30-07:00  -> DISABLE
  Window 2: 15:30-17:00  -> DISABLE

  Current : window1=False  window2=False
  Already correct -- nothing to do.
```

---

## Tuning the SOC targets

| Scenario | Window 1 morning target | Window 2 evening target |
|----------|------------------------|------------------------|
| Summer weekday | 50% | 55% |
| Summer weekend | 30% (no peak) | 40% (no peak) |
| Winter weekday | 65% | 95% |
| Winter weekend | 35% (no peak) | 50% (no peak) |

> Weekend targets are lower because G13s has **no true peak on weekends** in either season — the off-cheap hours are neutral rate, not expensive.

Defaults are calculated for a **9.4 kWh battery** with **~2000W average peak draw** and a **10% discharge floor** (~8.46 kWh usable). To adapt to your setup: `target% = (peak_hours × net_draw_kW) / 0.0846`

The `FOXESS_CLOUD_BONUS` value is added to all targets when cloud cover exceeds 60% (80% in winter), compensating for reduced solar refill during cheap midday hours.

---

## License

MIT
