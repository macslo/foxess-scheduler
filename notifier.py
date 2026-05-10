"""
Discord webhook notifier for FoxESS Grid Charge Scheduler.

Sends embedded messages on:
  - Window state changes (enable/disable)
  - Errors and warnings

Silent days = good days. No news means solar handled everything.
"""
import datetime
import os
import requests

WEBHOOK_URL = os.getenv("FOXESS_DISCORD_WEBHOOK", "")

COLOR_GREEN  = 0x2ecc71
COLOR_YELLOW = 0xf1c40f
COLOR_RED    = 0xe74c3c


def _solar_label(radiation: float, low_solar: bool) -> str:
    """Human-readable solar forecast label for Discord embed."""
    if datetime.datetime.now().hour >= 18:
        return "⏭ skipped (after 18:00)"
    bonus = "  ☁️ +bonus" if low_solar else "  ☀️"
    return f"{radiation:.0f} W/m²{bonus}"


def _send(payload: dict):
    if not WEBHOOK_URL:
        return
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"Warning: Discord notification failed ({e})")


def _embed(title: str, description: str, color: int, fields: list = None) -> dict:
    embed = {
        "title":       title,
        "description": description,
        "color":       color,
        "timestamp":   datetime.datetime.utcnow().isoformat(),
        "footer":      {"text": "FoxESS Scheduler"},
    }
    if fields:
        embed["fields"] = fields
    return {"embeds": [embed]}


def notify_run(
    sn: str,
    strategy_name: str,
    soc: float,
    radiation: float,
    low_solar: bool,
    morning_target: int,
    evening_target: int,
    enable1: bool,
    enable2: bool,
    start1: str, end1: str,
    start2: str, end2: str,
    changed: bool,
):
    """Send a state change notification if windows changed."""
    if not WEBHOOK_URL or not changed:
        return

    solar_label = _solar_label(radiation, low_solar)
    any_enabled = enable1 or enable2
    color       = COLOR_YELLOW if any_enabled else COLOR_GREEN
    icon        = "⚡" if any_enabled else "🌞"

    _send(_embed(
        title       = f"{icon} Window state changed",
        description = f"**Strategy:** {strategy_name}\n**Device:** `{sn}`",
        color       = color,
        fields      = [
            {"name": "SOC",            "value": f"{soc:.0f}%",        "inline": True},
            {"name": "Solar forecast", "value": solar_label,          "inline": True},
            {"name": f"Window 1 ({start1}–{end1})", "value": "✅ ENABLED" if enable1 else "⏸ disabled", "inline": True},
            {"name": f"Window 2 ({start2}–{end2})", "value": "✅ ENABLED" if enable2 else "⏸ disabled", "inline": True},
            {"name": "Morning target", "value": f"{morning_target}%", "inline": True},
            {"name": "Evening target", "value": f"{evening_target}%", "inline": True},
        ]
    ))


def notify_error(context: str, error: Exception):
    if not WEBHOOK_URL:
        return
    _send(_embed(
        title       = "❌ FoxESS Scheduler error",
        description = f"**{context}**\n```{error}```",
        color       = COLOR_RED,
    ))


def notify_warning(message: str):
    if not WEBHOOK_URL:
        return
    _send(_embed(
        title       = "⚠️ FoxESS Scheduler warning",
        description = message,
        color       = COLOR_YELLOW,
    ))
