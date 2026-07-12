"""EV charger status decoding and charge-current math."""

from __future__ import annotations

import math

from .model import Config

# Register 5015 enum (mirrors templates/sensors.yaml on the HA side).
EV_STATUS_MAP: dict[int, str] = {
    0: "disconnected",
    1: "connected",
    2: "charging",
    3: "charged",
    4: "waiting_for_sun",
    5: "waiting_for_rfid",
    6: "waiting_for_start",
    7: "low_soc",
    8: "ground_test_error",
    9: "welded_contacts_test_error",
    10: "cp_input_test_error",
    11: "residual_current_detected",
    12: "undervoltage_detected",
    13: "overvoltage_detected",
    14: "overheating_detected",
    20: "charging_limit",
    21: "starting_charge",
    22: "switching_to_3_phase",
    23: "switching_to_1_phase",
    24: "stopping_charge",
}

# States in which the charger may be driven (same set as the old automation).
VERBONDEN_STATUSSEN = {
    "connected",
    "charging",
    "starting_charge",
    "waiting_for_start",
    "waiting_for_sun",
    "charging_limit",
    "switching_to_3_phase",
    "switching_to_1_phase",
}

# States that end a session immediately (dwell-exempt off).
KLAAR_STATUSSEN = {"disconnected", "charged", "low_soc"}


def decodeer_status(raw: int | None) -> str | None:
    if raw is None:
        return None
    return EV_STATUS_MAP.get(int(raw), "unknown")


def zon_ampere(overschot_kw: float, ev_kw: float, config: Config) -> int:
    """Amps supported by surplus, counting the EV's own draw as available.

    Returns 0 when below the minimum current (ineligible).
    """
    beschikbaar_kw = overschot_kw + ev_kw
    ampere = math.floor(beschikbaar_kw * 1000.0 / config.ev_w_per_a)
    if ampere < config.ev_min_a:
        return 0
    return min(ampere, config.ev_max_a)


def beschikbaar_kw(overschot_kw: float, ev_kw: float) -> float:
    """Power available for the EV: surplus plus what it already consumes."""
    return overschot_kw + ev_kw
