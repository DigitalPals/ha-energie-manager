"""Peak-price arbitrage planner.

Finds the next expensive window in the price forecast and decides whether to
pre-charge the battery from the grid in cheap hours (VOORLADEN), hold the
battery back so it can cover the peak (VASTHOUDEN), or export battery surplus
to the grid during the peak (ONTLADEN).

Classification (what is a peak, ``prijs.is_piek``) is deliberately separate
from economics (is acting worth it): a "high" band on a flat day never
triggers charging because the spread margin is the actual go/no-go.

The planner only *proposes*; the engine gates on the feature switches, the
surplus channels, the negative-price overlay and the daily export cap. The
SoC proxy reports whole percents, so all energy comparisons use a ±0.9 kWh
(≈1.5% at 60 kWh) hysteresis band keyed on the previous tick's activity.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .energie import AccuPrognose, bruikbaar_kwh, prognose
from .model import ArbitrageActie, ArbitragePlan, Config, EngineState, Invoer
from .prijs import Venster, goedkoopste_slots, nu_goedkoop, piek_vensters

_HYSTERESE_KWH = 0.9  # ≈ 1.5% SoC at 60 kWh; masks whole-% SoC rounding


def plan_arbitrage(
    invoer: Invoer,
    config: Config,
    s: EngineState,
    nu: datetime,
    huislast_kw: float,
) -> ArbitragePlan:
    slots = invoer.prijs_slots
    if not slots:
        return ArbitragePlan(reden="geen prijsvooruitzicht")
    if invoer.soc is None:
        return ArbitragePlan(reden="accu-SoC onbekend")
    soc = invoer.soc

    prog = prognose(
        soc=soc,
        capaciteit_kwh=config.batterij_capaciteit_kwh,
        zon_restant_kwh=invoer.zon_vandaag_kwh,
        slots=slots,
        huislast_kw=huislast_kw,
        nu=nu,
        doel_soc=config.doel_soc_piek,
        reserve_soc=config.batterij_reserve_soc,
        zon_einde_uur=config.zon_einde_uur,
    )

    vensters = piek_vensters(slots, nu, config.piek_drempel_eur)
    if not vensters:
        return ArbitragePlan(
            reden="geen piek in prijsvooruitzicht", verwacht_vol_om=prog.vol_om
        )
    piek = vensters[0]

    if s.negatieve_prijs_actief:
        return _plan(
            ArbitrageActie.GEEN, "negatieve prijs actief", piek, prog=prog
        )

    if nu >= piek.start:
        return _in_piek(invoer, config, s, nu, huislast_kw, piek, prog, soc)
    return _voor_piek(invoer, config, s, nu, huislast_kw, piek, prog, soc)


def _plan(
    actie: ArbitrageActie,
    reden: str,
    piek: Venster,
    *,
    prog: AccuPrognose,
    behoefte_kwh: float = 0.0,
    verwacht_kwh: float = 0.0,
    tekort_kwh: float = 0.0,
    laad_slots: tuple[datetime, ...] = (),
    export_w: float = 0.0,
) -> ArbitragePlan:
    return ArbitragePlan(
        actie=actie,
        reden=reden,
        piek_start=piek.start,
        piek_einde=piek.einde,
        piek_gemiddeld=piek.gemiddeld_tarief,
        behoefte_kwh=round(behoefte_kwh, 2),
        verwacht_kwh_bij_piek=round(verwacht_kwh, 2),
        tekort_kwh=round(tekort_kwh, 2),
        laad_slots=laad_slots,
        export_w=export_w,
        verwacht_vol_om=prog.vol_om,
    )


def _voor_piek(
    invoer: Invoer,
    config: Config,
    s: EngineState,
    nu: datetime,
    huislast_kw: float,
    piek: Venster,
    prog: AccuPrognose,
    soc: float,
) -> ArbitragePlan:
    """Before the peak: pre-charge in cheap slots or hold the battery."""
    vloer = max(config.batterij_reserve_soc, config.piek_reserve_soc)
    piek_uren = (piek.einde - piek.start).total_seconds() / 3600.0
    behoefte = huislast_kw * piek_uren
    verwacht = bruikbaar_kwh(
        prog.soc_op(piek.start), vloer, config.batterij_capaciteit_kwh
    )
    tekort = behoefte - verwacht
    was_actief = s.piek_vasthouden_actief or s.netladen_actief
    drempel = -_HYSTERESE_KWH if was_actief else _HYSTERESE_KWH
    venster_str = f"piek {piek.start:%H:%M}–{piek.einde:%H:%M} (gem €{piek.gemiddeld_tarief:.2f})"
    if tekort <= drempel:
        return _plan(
            ArbitrageActie.GEEN,
            f"accu dekt {venster_str}",
            piek,
            prog=prog,
            behoefte_kwh=behoefte,
            verwacht_kwh=verwacht,
            tekort_kwh=tekort,
        )

    # --- VOORLADEN: pick the cheapest pre-peak slots that clear the margin ---
    eta = max(0.01, config.rendement_rondrit)
    laad_kw = config.max_laadvermogen_net_w / 1000.0
    laad_uren = max(1, math.ceil(max(0.0, tekort) / max(0.1, laad_kw * eta)))
    kandidaten = goedkoopste_slots(slots := invoer.prijs_slots, laad_uren, piek.start)
    gekozen = tuple(
        sl
        for sl in kandidaten
        if piek.gemiddeld_tarief - sl.tarief / eta >= config.arbitrage_min_marge
    )
    laad_slots = tuple(sl.start for sl in gekozen)
    nu_in_laadslot = any(
        sl.start <= nu < sl.start + timedelta(hours=1) for sl in gekozen
    )
    if nu_in_laadslot and soc < config.doel_soc_piek:
        return _plan(
            ArbitrageActie.VOORLADEN,
            f"netladen voor {venster_str}, tekort {tekort:.1f} kWh",
            piek,
            prog=prog,
            behoefte_kwh=behoefte,
            verwacht_kwh=verwacht,
            tekort_kwh=tekort,
            laad_slots=laad_slots,
        )

    # --- VASTHOUDEN: reserve the battery, run the house on the grid ---
    tarief_nu = invoer.tarief
    goedkoop_nu = nu_goedkoop(slots, nu, tarief_nu, config.prijsplafond_batterij)
    if (
        not goedkoop_nu
        and tarief_nu is not None
        and tarief_nu >= 0
        and piek.gemiddeld_tarief - tarief_nu >= config.arbitrage_min_marge
    ):
        return _plan(
            ArbitrageActie.VASTHOUDEN,
            f"accu vasthouden voor {venster_str}, tekort {tekort:.1f} kWh",
            piek,
            prog=prog,
            behoefte_kwh=behoefte,
            verwacht_kwh=verwacht,
            tekort_kwh=tekort,
            laad_slots=laad_slots,
        )
    return _plan(
        ArbitrageActie.GEEN,
        f"{venster_str}: marge te klein of goedkoop uur",
        piek,
        prog=prog,
        behoefte_kwh=behoefte,
        verwacht_kwh=verwacht,
        tekort_kwh=tekort,
        laad_slots=laad_slots,
    )


def _in_piek(
    invoer: Invoer,
    config: Config,
    s: EngineState,
    nu: datetime,
    huislast_kw: float,
    piek: Venster,
    prog: AccuPrognose,
    soc: float,
) -> ArbitragePlan:
    """Inside the peak: export true surplus, else plain self-consumption."""
    vloer = max(config.batterij_reserve_soc, config.piek_reserve_soc)
    rest_uren = max(0.0, (piek.einde - nu).total_seconds() / 3600.0)
    beschikbaar = bruikbaar_kwh(soc, vloer, config.batterij_capaciteit_kwh)
    behoefte = huislast_kw * rest_uren
    surplus = beschikbaar - behoefte
    venster_str = f"piek tot {piek.einde:%H:%M} (gem €{piek.gemiddeld_tarief:.2f})"
    drempel = 0.0 if s.piek_export_actief else _HYSTERESE_KWH

    tarief_nu = invoer.tarief
    eta = max(0.01, config.rendement_rondrit)
    if prog.vol_om is not None:
        herlaad_kost = 0.0  # solar refills the battery today anyway
    else:
        toekomst = [sl.tarief for sl in invoer.prijs_slots if sl.start >= piek.einde]
        herlaad_kost = (
            min(toekomst) / eta if toekomst else float("inf")
        )

    if (
        surplus > drempel
        and rest_uren > 0
        and tarief_nu is not None
        and tarief_nu >= config.export_bodem_eur
        and tarief_nu - herlaad_kost >= config.arbitrage_min_marge
    ):
        export_w = min(
            surplus / rest_uren * 1000.0,
            config.max_export_w,
            config.feed_in_herstel_w,
            config.ontlading_herstel_w,
        )
        return _plan(
            ArbitrageActie.ONTLADEN,
            f"teruglevering {export_w:.0f} W in {venster_str}, surplus {surplus:.1f} kWh",
            piek,
            prog=prog,
            behoefte_kwh=behoefte,
            verwacht_kwh=beschikbaar,
            tekort_kwh=-surplus,
            export_w=round(export_w),
        )
    return _plan(
        ArbitrageActie.GEEN,
        f"zelfverbruik door {venster_str}",
        piek,
        prog=prog,
        behoefte_kwh=behoefte,
        verwacht_kwh=beschikbaar,
        tekort_kwh=-surplus,
    )
