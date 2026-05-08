import os

# ══════════════════════════════════════════════════════════════════════════════
# FoxESS Grid Charge Scheduler — configuration
# This file is committed to the repo. Edit defaults here.
# Any value can be overridden in .env if needed.
# Secrets (API key, device SN, location) belong in .env only.
# ══════════════════════════════════════════════════════════════════════════════

# ── Active hours ──────────────────────────────────────────────────────────────
# Script exits immediately outside this window — no API calls, no cloud checks.
ACTIVE_HOUR_START = int(os.getenv("FOXESS_ACTIVE_HOUR_START", "6"))
ACTIVE_HOUR_END   = int(os.getenv("FOXESS_ACTIVE_HOUR_END",   "21"))

# ── Window proximity ──────────────────────────────────────────────────────────
# How many minutes before a window start the script should activate.
# Cron runs every 2 minutes — lead of 3 min ensures at least one run fires
# before each window opens, regardless of which window times are configured.
WINDOW_LEAD_MINUTES = int(os.getenv("FOXESS_WINDOW_LEAD_MINUTES", "3"))

# ── Tariff mode ───────────────────────────────────────────────────────────────
# g13s   -> automatic Tauron G13s seasonal schedule (default)
# manual -> use your own windows defined below
TARIFF = os.getenv("FOXESS_TARIFF", "g13s").strip().lower()

# ── Manual windows (only used when TARIFF=manual) ─────────────────────────────
# FOXESS_CHARGE1_ENABLE / FOXESS_CHARGE2_ENABLE: always | never | weekdays | weekends
CHARGE1_START  = os.getenv("FOXESS_CHARGE1_START",  "01:00")
CHARGE1_END    = os.getenv("FOXESS_CHARGE1_END",    "05:00")
CHARGE1_ENABLE = os.getenv("FOXESS_CHARGE1_ENABLE", "weekdays")
CHARGE2_START  = os.getenv("FOXESS_CHARGE2_START",  "13:00")
CHARGE2_END    = os.getenv("FOXESS_CHARGE2_END",    "15:00")
CHARGE2_ENABLE = os.getenv("FOXESS_CHARGE2_ENABLE", "never")

# ── G13s options ──────────────────────────────────────────────────────────────
# Enable midday charge window on weekends
# (weekends have no true peak, but you may still want to top up cheaply)
G13S_WEEKEND_MIDDAY = os.getenv("FOXESS_G13S_WEEKEND_MIDDAY", "false").strip().lower() == "true"

# ── Tauron G13s tariff reference ──────────────────────────────────────────────
# Source: tanie_godziny_jak_dziala_taryfa_g13s_12_2025.pdf
# Total = sales price (ceny sprzedażowe) + distribution variable (ceny dystrybucyjne)
#
# WINTER (1 Oct – 31 Mar)
#   Weekdays:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6089 + 0.1346 = 0.7435 zł/kWh)
#     🟡 CHEAP midday:  10:00 – 15:00  (0.6827 + 0.2459 = 0.9286 zł/kWh)
#     🔴 PEAK:          07:00 – 10:00  and  15:00 – 21:00  (0.8723 + 0.4098 = 1.2821 zł/kWh)
#   Weekends:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6089 + 0.1346 = 0.7435 zł/kWh)
#     🟢 CHEAPEST mid:  10:00 – 15:00  (0.4121 + 0.1476 = 0.5597 zł/kWh)
#     🟡 NEUTRAL:       07:00 – 10:00  and  15:00 – 21:00  (0.5258 + 0.2411 = 0.7669 zł/kWh)
#
# SUMMER (1 Apr – 30 Sep)
#   Weekdays:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6212 + 0.1346 = 0.7558 zł/kWh)
#     🟢 CHEAPEST mid:  09:00 – 17:00  (0.3383 + 0.1230 = 0.4613 zł/kWh)  <- solar hours!
#     🔴 PEAK:          07:00 – 09:00  and  17:00 – 21:00  (0.8723 + 0.3496 = 1.2219 zł/kWh)
#   Weekends:
#     🟡 CHEAP night:   21:00 – 07:00  (0.6212 + 0.1346 = 0.7558 zł/kWh)
#     🟢 CHEAPEST mid:  09:00 – 17:00  (0.1390 + 0.0492 = 0.1882 zł/kWh!)  <- very cheap
#     🟡 NEUTRAL:       07:00 – 09:00  and  17:00 – 21:00  (0.3526 + 0.1446 = 0.4972 zł/kWh)
#
# Weekday peak is ~3x the cheapest rate — well worth avoiding.
# Weekends have NO true peak in either season.

# ── SOC charge targets (%) ────────────────────────────────────────────────────
# Window 1 (morning 06:30-07:00): minimum SOC before the morning peak block.
# Window 2 (evening 15:30-17:00): minimum SOC before night — goal is to be
#   near-full by 21:00 so the battery is ready for overnight draw. The current
#   SOC at check time already reflects the day's solar production, so a high
#   target here naturally enables charging only when solar hasn't done the job.
#
# Weekend targets are lower — no true peak, only neutral rates apply.
TARGET_SUMMER_WEEKDAY_MORNING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKDAY_MORNING", "15"))  # 1h peak × 1kW / 0.0846
TARGET_SUMMER_WEEKDAY_EVENING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKDAY_EVENING", "85"))
TARGET_SUMMER_WEEKEND_MORNING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKEND_MORNING", "15"))
TARGET_SUMMER_WEEKEND_EVENING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKEND_EVENING", "85"))
TARGET_WINTER_WEEKDAY_MORNING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKDAY_MORNING", "65"))
TARGET_WINTER_WEEKDAY_EVENING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKDAY_EVENING", "95"))
TARGET_WINTER_WEEKEND_MORNING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKEND_MORNING", "35"))
TARGET_WINTER_WEEKEND_EVENING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKEND_EVENING", "85"))

# Cloud bonus: extra % added to targets when cloud cover exceeds threshold.
#
# CLOUD_BONUS_MORNING: applied to window 1 targets. Cloud forecast predicts the
#   coming day's solar — high cloud means solar won't recover the battery, so
#   charge more from grid now. Higher bonus makes sense here.
#
# CLOUD_BONUS_EVENING: applied to window 2 targets. Current SOC at 15:30 already
#   reflects what the day's solar has delivered — cloud bonus only adds a small
#   nudge for borderline cases where SOC is just above the base target.
#   Lower bonus here since SOC is a better signal than forecast at this point.
CLOUD_BONUS_MORNING = int(os.getenv("FOXESS_CLOUD_BONUS_MORNING", "10"))  # low usage in morning regardless of weather
CLOUD_BONUS_EVENING = int(os.getenv("FOXESS_CLOUD_BONUS_EVENING", "15"))  # pushes evening target to 95% cap on cloudy days
