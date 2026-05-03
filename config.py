import os

# ══════════════════════════════════════════════════════════════════════════════
# FoxESS Grid Charge Scheduler — configuration
# This file is committed to the repo. Edit defaults here.
# Any value can be overridden in .env if needed.
# Secrets (API key, device SN, location) belong in .env only.
# ══════════════════════════════════════════════════════════════════════════════

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
# Minimum SOC% the battery should reach before each peak block.
# Calculated for 9.4 kWh battery, ~2000W peak draw, 10% discharge floor
# (~8.46 kWh usable). Formula: target% = (peak_hours × net_draw_kW) / 0.0846
#
# Window 1 = morning top-up before 07:00
# Window 2 = pre-evening charge before 17:00 (summer) / 15:00 (winter)
#
# Weekend targets are lower — no true peak, only neutral rates apply.
# CLOUD_BONUS is added to all targets when cloud cover > 60% (low solar).
TARGET_SUMMER_WEEKDAY_MORNING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKDAY_MORNING", "50"))
TARGET_SUMMER_WEEKDAY_EVENING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKDAY_EVENING", "55"))
TARGET_SUMMER_WEEKEND_MORNING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKEND_MORNING", "30"))
TARGET_SUMMER_WEEKEND_EVENING = int(os.getenv("FOXESS_TARGET_SUMMER_WEEKEND_EVENING", "40"))
TARGET_WINTER_WEEKDAY_MORNING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKDAY_MORNING", "65"))
TARGET_WINTER_WEEKDAY_EVENING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKDAY_EVENING", "95"))
TARGET_WINTER_WEEKEND_MORNING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKEND_MORNING", "35"))
TARGET_WINTER_WEEKEND_EVENING = int(os.getenv("FOXESS_TARGET_WINTER_WEEKEND_EVENING", "50"))

# Extra % added to all targets when cloud cover exceeds threshold (poor solar day)
CLOUD_BONUS = int(os.getenv("FOXESS_CLOUD_BONUS", "20"))
