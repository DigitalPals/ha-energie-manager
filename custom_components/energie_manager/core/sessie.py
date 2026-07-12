"""EV-laadsessie boekhouding: energie- en kostensplitsing per tick.

Energy ground truth is the charger's own cumulative session meter (kWh,
resets per session by firmware). Each tick's meter delta is split into a
grid share (priced at the live dynamic tariff, signed — negative prices
yield negative cost) and a free share (solar surplus + home battery, €0).
Attribution rule: grid share = min(ev_kw, max(0, grid_import_kw)) / ev_kw.

Known limitation: if the meter is unavailable for a few ticks, the gap
delta is attributed entirely with the ratio of the tick where it returns;
if the firmware resets the meter during such a gap, the old session's tail
folds into the new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .ev import KLAAR_STATUSSEN, VERBONDEN_STATUSSEN
from .model import SessieRecord, SessieState

DELTA_MIN_KWH = 0.001  # ignore meter noise below this (meter quantum is 0.01)
MIN_EV_KW = 0.1  # below this, EV power is untrusted for the ratio
NET_MIN_KW = 0.05  # grid-import threshold for the stale-EV-power fallback
MAX_HISTORIE = 10


@dataclass(frozen=True)
class SessieResultaat:
    gestart: bool = False
    beeindigd: bool = False


def _grid_ratio(ev_power_w: float | None, net_vermogen_w: float | None) -> float:
    """Fraction of this tick's energy attributed to grid import (0..1)."""
    if net_vermogen_w is None:
        return 1.0  # unknown split: price it (overestimating beats hiding cost)
    net_kw = max(net_vermogen_w, 0.0) / 1000.0
    ev_kw = max(ev_power_w or 0.0, 0.0) / 1000.0
    if ev_kw < MIN_EV_KW:
        # meter climbs but the power sensor is stale/zero: all-or-nothing
        return 1.0 if net_kw > NET_MIN_KW else 0.0
    return min(net_kw, ev_kw) / ev_kw


def _rond_af(s: SessieState, historie: list[SessieRecord], einde: datetime) -> None:
    """Finalize the running session into the history and reset the state."""
    if s.start is not None:
        historie.insert(
            0,
            SessieRecord(
                start=s.start,
                einde=einde,
                energie_kwh=s.energie_kwh,
                energie_gratis_kwh=s.energie_gratis_kwh,
                energie_net_kwh=s.energie_net_kwh,
                energie_ongeprijsd_kwh=s.energie_ongeprijsd_kwh,
                kosten_eur=s.kosten_eur,
            ),
        )
        del historie[MAX_HISTORIE:]
    # Reset the accumulators; keep laatste_meter_kwh so the next tick's
    # delta (or reset detection) stays correct.
    s.actief = False
    s.start = None
    s.energie_kwh = 0.0
    s.energie_gratis_kwh = 0.0
    s.energie_net_kwh = 0.0
    s.energie_ongeprijsd_kwh = 0.0
    s.kosten_eur = 0.0


def _start(s: SessieState, nu: datetime) -> None:
    s.actief = True
    s.start = nu


def _boek(
    s: SessieState,
    delta: float,
    ev_power_w: float | None,
    net_vermogen_w: float | None,
    tarief: float | None,
) -> None:
    """Attribute one meter delta to the running session."""
    ratio = _grid_ratio(ev_power_w, net_vermogen_w)
    net_delta = delta * ratio
    s.energie_kwh += delta
    s.energie_net_kwh += net_delta
    s.energie_gratis_kwh += delta - net_delta
    if tarief is not None:
        s.kosten_eur += net_delta * tarief
    else:
        s.energie_ongeprijsd_kwh += net_delta


def update(
    s: SessieState,
    historie: list[SessieRecord],
    meter_kwh: float | None,
    ev_status: str | None,
    ev_power_w: float | None,
    net_vermogen_w: float | None,
    tarief: float | None,
    nu: datetime,
) -> SessieResultaat:
    """Advance the session accountant one tick. Mutates ``s``/``historie``."""
    gestart = False
    beeindigd = False

    # Charger says the session is over (status None must NOT end a session:
    # a sensor outage may not split sessions).
    if s.actief and ev_status in KLAAR_STATUSSEN:
        _rond_af(s, historie, nu)
        beeindigd = True

    if meter_kwh is None:
        return SessieResultaat(gestart=gestart, beeindigd=beeindigd)

    if s.laatste_meter_kwh is None:
        # First sighting: baseline only; adopt a session already in progress.
        if not s.actief and meter_kwh > DELTA_MIN_KWH and ev_status in VERBONDEN_STATUSSEN:
            _start(s, nu)
            gestart = True
        s.laatste_meter_kwh = meter_kwh
        return SessieResultaat(gestart=gestart, beeindigd=beeindigd)

    delta = meter_kwh - s.laatste_meter_kwh

    if delta < -DELTA_MIN_KWH:
        # Meter reset: the charger started a new session on its own.
        if s.actief:
            _rond_af(s, historie, nu)
            beeindigd = True
        delta = meter_kwh  # the fresh meter itself is this tick's delta

    if delta > DELTA_MIN_KWH:
        if not s.actief and (ev_status is None or ev_status in VERBONDEN_STATUSSEN):
            # Energy is the ground truth for "a session exists"; a 0 kWh
            # plug-in never creates a record.
            _start(s, nu)
            gestart = True
        if s.actief:
            _boek(s, delta, ev_power_w, net_vermogen_w, tarief)

    s.laatste_meter_kwh = meter_kwh
    return SessieResultaat(gestart=gestart, beeindigd=beeindigd)
