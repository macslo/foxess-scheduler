# FoxESS Grid Charge Scheduler

Automatically manages battery grid charging windows on a FoxESS inverter, optimised for the **Tauron G13s time-of-use tariff** (Poland). Runs via cron every 2 minutes but exits immediately unless near a charge window — no unnecessary API calls.

## How it works

The script selects a **charging strategy** based on season and day type, then decides whether to enable or disable each FoxESS force-charge window based on:

- **Current battery SOC** — charges only if battery is below the target for the upcoming peak block
- **Season** (summer/winter) and **day type** (weekday/weekend) — G13s has different peak hours and no true peak on weekends
- **Solar forecast** — uses shortwave radiation (W/m²) from Open-Meteo to detect poor solar days and raise targets accordingly
- **Window proximity** — only runs fully when within a few minutes of a window start; all other runs exit instantly
- **Window freeze** — once a window's end time passes, its state is no longer touched to avoid spurious changes

### Tauron G13s tariff schedule

Prices from official Tauron PDF (12/2025). Total = sales + distribution variable component.

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

> Peak hours on weekdays are roughly **3× more expensive** than cheap midday. Weekends have **no true peak** in either season. For current prices see the [Tauron G13s tariff page](https://www.tauron.pl/dla-domu/prad/prad-z-usluga/tanie-godziny).

### Charge windows

Sized for a ~10 kW grid charge rate. Window length covers worst-case SOC top-up with margin.

| Strategy | Window 1 (morning) | Window 2 (evening) |
|----------|-------------------|-------------------|
| Summer weekday | 06:50 – 07:00 (10 min) | 16:20 – 17:00 (40 min) |
| Summer weekend | 06:50 – 07:00 — disabled | 16:20 – 17:00 — disabled by default |
| Winter weekday | 06:30 – 07:00 (30 min) | 14:20 – 15:00 (40 min) |
| Winter weekend | 06:50 – 07:00 — disabled | 14:20 – 15:00 — disabled by default |

---

## File structure

```
foxess_grid_charge_scheduler.py  ← main orchestration + API calls
strategies.py                    ← charging strategy per season/day type
weather.py                       ← solar forecast (Open-Meteo shortwave radiation)
notifier.py                      ← Discord webhook notifications
config.py                        ← all tunable settings (committed to repo)
.env                             ← secrets only — never committed
.env.example                     ← template for .env
update_and_run.sh                ← git pull + run (used by cron)
test_scheduler.py                ← unit tests
```

---

## Requirements

- Python 3.7+
- `requests` library: `pip install requests`
- FoxESS Cloud API key ([foxesscloud.com](https://www.foxesscloud.com) → Avatar → Personal Centre → API Management)

---

## Installation

```bash
git clone https://github.com/macslo/foxess-scheduler.git
cd foxess-scheduler
pip install requests
cp .env.example .env
# edit .env with your API key and location
```

---

## Configuration

### `.env` — secrets only, never committed

```ini
# Required
FOXESS_API_KEY=your_api_key_here

# Device serial — leave as "auto" to detect automatically
FOXESS_SN=auto

# Your location for solar forecast
# Default in config.py is Gliwice, Poland
FOXESS_LAT=50.2849
FOXESS_LON=18.6717

# Discord webhook for notifications (optional)
# Discord channel → Edit → Integrations → Webhooks → New Webhook → Copy URL
FOXESS_DISCORD_WEBHOOK=
```

### `config.py` — all tunable settings, committed to repo

Key settings (see `config.py` for full list with comments):

```python
# Active hours — script skips entirely outside this window
ACTIVE_HOUR_START = 6
ACTIVE_HOUR_END   = 21

# Minutes before window start to activate (cron runs every 2 min)
WINDOW_LEAD_MINUTES = 3

# SOC targets — minimum % before each peak block
TARGET_SUMMER_WEEKDAY_MORNING = 15   # 1h peak, low usage
TARGET_SUMMER_WEEKDAY_EVENING = 85   # goal: near-full before night
TARGET_SUMMER_WEEKEND_MORNING = 15
TARGET_SUMMER_WEEKEND_EVENING = 85
TARGET_WINTER_WEEKDAY_MORNING = 65   # 3h peak
TARGET_WINTER_WEEKDAY_EVENING = 95   # 6h peak — worst block
TARGET_WINTER_WEEKEND_MORNING = 35
TARGET_WINTER_WEEKEND_EVENING = 85

# Cloud bonus — added to targets when solar forecast is poor
CLOUD_BONUS_MORNING = 10   # morning: low usage regardless of weather
CLOUD_BONUS_EVENING = 10   # evening: current SOC already reflects the day's solar
```

All `config.py` values can be overridden in `.env` using the `FOXESS_` prefix.

---

## Running on QNAP NAS

### Manual run

```bash
python3 foxess_grid_charge_scheduler.py
```

### Force run (bypass window proximity check — still respects SOC logic, useful for testing)

```bash
python3 foxess_grid_charge_scheduler.py --force
```

### Cron setup

The script self-manages when to run — cron just needs to fire every 2 minutes:

```
*/2 * * * * update_and_run.sh
```

`update_and_run.sh` pulls the latest version from GitHub then runs the scheduler. Copy it to your QNAP and make it executable:

---

## Discord notifications

The scheduler sends Discord embed messages on:
- **Window state change** — when a window is enabled or disabled (yellow/green)
- **Errors** — API failures, missing config (red)
- **Daily summary** — first run each day with SOC, solar forecast and window states (blue)

Set `FOXESS_DISCORD_WEBHOOK` in `.env` to activate. Leave blank to disable.

---

## Example output

```
[RUN] 2026-05-04T06:51:23.456789
  Solar   : 312 W/m²  (solar hours 09:00–11:00, 2h avg)  ☀️ good
FoxESS Grid Charge Scheduler
  Device  : XYZ
  Today   : Monday, 04 May 2026
  Location: 50.2849, 18.6717
  Strategy: G13s SUMMER weekday
  SOC     : 22.0%  (morning target=15%  evening target=85%)
  Window 1: 06:50-07:00  -> ENABLE
  Window 2: 16:20-17:00  -> ENABLE

  Current : window1=False  window2=False
  Done: window1=ENABLED  window2=ENABLED
```

```
[RUN] 2026-05-04T10:15:00.000000
[SKIP] not near any window  (w1=06:50–07:00  w2=16:20–17:00  lead=3min)
```

---

## Tuning SOC targets

Default targets are calculated for a **9.4 kWh battery**, **~2000W peak draw**, **10% discharge floor** (~8.46 kWh usable).

| Scenario | Morning target | Evening target |
|----------|---------------|---------------|
| Summer weekday | 15% | 85% |
| Summer weekend | 15% (no peak) | 85% |
| Winter weekday | 65% | 95% |
| Winter weekend | 35% (no peak) | 85% |

Evening targets are high because the goal is to be **near-full by 21:00** — the current SOC at check time already reflects the day's solar production, so a high target naturally enables charging only when solar hasn't done the job.

To adapt to your battery: `target% = (peak_hours × net_draw_kW) / (battery_kWh × usable_fraction / 100)`
/
---

## Running tests

```bash
python3 -m pytest test_scheduler.py -v
# or without pytest:
python3 test_scheduler.py
```

---

## License

MIT