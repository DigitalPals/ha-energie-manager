"""Battery energy model: a simple, explainable hourly SoC simulation.

Predicts the SoC path for the coming hours from the current SoC, the
remaining solar forecast for today and an average house load. Solar energy
is distributed over the remaining daylight hours using Zonneplan's per-slot
``solar_percentage`` when available, otherwise spread uniformly until
``zon_einde_uur``. Tomorrow's solar is deliberately ignored: the horizon of
interest is "before today's peak".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import PrijsSlot


@dataclass(frozen=True)
class AccuPrognose:
    """Hourly SoC path (slot start -> SoC at that moment)."""

    pad: tuple[tuple[datetime, float], ...]
    vol_om: datetime | None  # first time SoC >= doel_soc, None if never

    def soc_op(self, t: datetime) -> float:
        """SoC at time ``t`` (last known point at or before ``t``)."""
        if not self.pad:
            return 0.0
        soc = self.pad[0][1]
        for tijd, waarde in self.pad:
            if tijd > t:
                break
            soc = waarde
        return soc


def bruikbaar_kwh(soc: float, vloer_soc: float, capaciteit_kwh: float) -> float:
    """Usable battery energy above a SoC floor."""
    return capaciteit_kwh * max(0.0, soc - vloer_soc) / 100.0


def _zon_gewichten(
    slots: tuple[PrijsSlot, ...],
    uren: list[datetime],
    zon_einde_uur: int,
) -> list[float]:
    """Weight per hour for distributing the remaining solar forecast.

    Zonneplan's per-slot solar_percentage wins when present; else uniform
    over the hours before ``zon_einde_uur``. Weights sum to 1 (or all 0).
    """
    pct_per_start = {
        s.start: s.zon_pct for s in slots if s.zon_pct is not None and s.zon_pct > 0
    }
    gewichten = [float(pct_per_start.get(uur, 0.0)) for uur in uren]
    totaal = sum(gewichten)
    if totaal > 0:
        return [g / totaal for g in gewichten]
    # uniform fallback over remaining daylight hours
    dag = [1.0 if uur.hour < zon_einde_uur else 0.0 for uur in uren]
    totaal = sum(dag)
    return [g / totaal for g in dag] if totaal > 0 else dag


def prognose(
    *,
    soc: float,
    capaciteit_kwh: float,
    zon_restant_kwh: float | None,
    slots: tuple[PrijsSlot, ...],
    huislast_kw: float,
    nu: datetime,
    doel_soc: float,
    reserve_soc: float,
    zon_einde_uur: int,
    horizon_uren: int = 24,
) -> AccuPrognose:
    """Simulate the SoC hour by hour (first hour pro-rated)."""
    if capaciteit_kwh <= 0:
        return AccuPrognose(pad=((nu, soc),), vol_om=nu if soc >= doel_soc else None)

    huidig_uur = nu.replace(minute=0, second=0, microsecond=0)
    uren = [huidig_uur + timedelta(hours=h) for h in range(horizon_uren)]
    # solar only lands on hours of *today*; the horizon may cross midnight
    vandaag = nu.date()
    zon_uren = [u for u in uren if u.date() == vandaag]
    gewichten = dict(
        zip(zon_uren, _zon_gewichten(slots, zon_uren, zon_einde_uur), strict=True)
    )
    zon_totaal = max(0.0, zon_restant_kwh or 0.0)

    pad: list[tuple[datetime, float]] = [(nu, soc)]
    vol_om: datetime | None = nu if soc >= doel_soc else None
    huidige_soc = soc
    for uur in uren:
        einde = uur + timedelta(hours=1)
        if einde <= nu:
            continue
        fractie = (einde - max(uur, nu)).total_seconds() / 3600.0
        zon_kwh = zon_totaal * gewichten.get(uur, 0.0) * fractie
        netto_kwh = zon_kwh - huislast_kw * fractie
        huidige_soc += netto_kwh / capaciteit_kwh * 100.0
        huidige_soc = min(100.0, max(reserve_soc, huidige_soc))
        pad.append((einde, huidige_soc))
        if vol_om is None and huidige_soc >= doel_soc:
            vol_om = einde
    return AccuPrognose(pad=tuple(pad), vol_om=vol_om)
