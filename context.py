"""
Charge context for FoxESS Grid Charge Scheduler.

Passed to strategy methods so they have full situational awareness
without changing method signatures when new data becomes available.
"""
from dataclasses import dataclass


@dataclass
class ChargeContext:
    low_solar: bool    # solar forecast below threshold
    soc:       float   # current battery SOC %
    pv_kw:     float   # current PV output kW
    winter:    bool    # True if Oct–Mar
