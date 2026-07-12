"""Legionella protection: hold tracking, weekly planning, forcing.

Health-critical: the boiler must reach >= (target - 0.1) °C for a continuous
20 minutes at least every 7 days. The tracker runs every tick regardless of
who is heating (a good solar day self-satisfies the week). The planner
schedules a forced run in the 14:00-20:00 window before the deadline lapses,
preferring cheap/sunny days when the deadline allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import Config, LegionellaState, PrijsSlot
from .prijs import goedkoopste_venster


@dataclass(frozen=True)
class HoldResultaat:
    succes: bool
    bezig: bool  # a streak is currently building
    hold_minuten: float  # progress of the current streak


def _drempel(config: Config) -> float:
    # "above 60.9" semantics from the live automation: >= target - 0.1
    return config.boiler_doel_c - 0.1


def update_hold(
    state: LegionellaState, temp: float | None, nu: datetime, config: Config
) -> HoldResultaat:
    """Advance the >=61° hold tracker. Mutates ``state``; returns progress.

    A dip below threshold (or missing data) pauses the hold; if the pause
    exceeds the gap limit the hold resets. Paused time never counts toward
    the 20 minutes (hold_start shifts forward by the pause duration).
    """
    gap = timedelta(minutes=config.legionella_gap_minuten)

    if temp is None or temp < _drempel(config):
        if state.hold_start is not None:
            if state.hold_pauze_sinds is None:
                state.hold_pauze_sinds = nu
            elif nu - state.hold_pauze_sinds > gap:
                state.hold_start = None
                state.hold_pauze_sinds = None
        return HoldResultaat(succes=False, bezig=False, hold_minuten=0.0)

    # temp at/above threshold
    if state.hold_start is None:
        state.hold_start = nu
        state.hold_pauze_sinds = None
    elif state.hold_pauze_sinds is not None:
        pauze = nu - state.hold_pauze_sinds
        if pauze > gap:
            state.hold_start = nu  # gap too long: restart
        else:
            state.hold_start += pauze  # paused time doesn't count
        state.hold_pauze_sinds = None

    verstreken = (nu - state.hold_start).total_seconds() / 60.0
    if verstreken >= config.legionella_hold_minuten:
        state.laatste_succes = nu
        state.hold_start = None
        state.hold_pauze_sinds = None
        state.forceer_actief = False
        state.gepland_voor = None
        return HoldResultaat(succes=True, bezig=False, hold_minuten=verstreken)
    return HoldResultaat(succes=False, bezig=True, hold_minuten=verstreken)


@dataclass(frozen=True)
class PlanResultaat:
    forceer: bool  # relay must be forced on right now
    gepland_voor: datetime | None
    reden: str


def _venster_start(dag: datetime, config: Config) -> datetime:
    return dag.replace(
        hour=config.legionella_venster_start_uur,
        minute=config.legionella_plan_minuut,
        second=0,
        microsecond=0,
    )


def plan(
    state: LegionellaState,
    nu: datetime,
    slots: tuple[PrijsSlot, ...],
    zon_vandaag_kwh: float | None,
    zon_morgen_kwh: float | None,
    config: Config,
) -> PlanResultaat:
    """Decide whether the boost relay must be forced for legionella.

    Mutates ``state`` (forceer_actief / gepland_voor bookkeeping).
    """
    eind_uur = config.legionella_venster_eind_uur

    # 20:00 hard stop: never (keep) forcing in the evening/night.
    buiten_uren = nu.hour >= eind_uur or nu.hour < config.legionella_venster_start_uur - 6
    if buiten_uren and state.forceer_actief:
        state.forceer_actief = False
        state.gepland_voor = None  # replan next day
        return PlanResultaat(False, None, "avondstop: cyclus afgebroken tot morgen")

    if state.forceer_actief:
        return PlanResultaat(True, state.gepland_voor, "legionella-cyclus loopt")

    if state.laatste_succes is None:
        # never succeeded (or fresh install without migration): treat as overdue
        deadline = nu
    else:
        deadline = state.laatste_succes + timedelta(days=config.legionella_interval_dagen)

    dagen_sinds = (
        (nu - state.laatste_succes).total_seconds() / 86400.0
        if state.laatste_succes is not None
        else float(config.legionella_interval_dagen)
    )

    binnen_venster = config.legionella_venster_start_uur <= nu.hour < eind_uur

    # Deadline passed: force as soon as the window allows.
    if nu >= deadline:
        if binnen_venster:
            state.forceer_actief = True
            return PlanResultaat(True, None, "deadline verstreken: cyclus geforceerd")
        vandaag_start = _venster_start(nu, config)
        gepland = vandaag_start if nu < vandaag_start else _venster_start(
            nu + timedelta(days=1), config
        )
        state.gepland_voor = gepland
        return PlanResultaat(False, gepland, "deadline verstreken: wacht op venster")

    # From plan-day onward: schedule opportunistically.
    if dagen_sinds >= config.legionella_plan_dag:
        vandaag_start = _venster_start(nu, config)
        morgen_start = _venster_start(nu + timedelta(days=1), config)

        uitstel = False
        reden_uitstel = ""
        # Defer to tomorrow only if the deadline still allows a full window
        # tomorrow AND tomorrow is materially sunnier or cheaper.
        if deadline > morgen_start + timedelta(hours=2):
            if (
                zon_morgen_kwh is not None
                and zon_vandaag_kwh is not None
                and zon_morgen_kwh >= 1.25 * max(zon_vandaag_kwh, 0.1)
            ):
                uitstel = True
                reden_uitstel = "morgen zonniger"
            else:
                venster_vandaag = goedkoopste_venster(
                    tuple(s for s in slots if s.start.date() == nu.date()), 1
                )
                venster_morgen = goedkoopste_venster(
                    tuple(s for s in slots if s.start.date() > nu.date()), 1
                )
                if (
                    venster_vandaag is not None
                    and venster_morgen is not None
                    and venster_morgen.gemiddeld_tarief
                    < venster_vandaag.gemiddeld_tarief - 0.05
                ):
                    uitstel = True
                    reden_uitstel = "morgen goedkoper"

        gepland = morgen_start if uitstel else vandaag_start
        if not uitstel and nu >= vandaag_start and binnen_venster:
            state.forceer_actief = True
            state.gepland_voor = None
            return PlanResultaat(True, None, "geplande legionella-cyclus gestart")
        if not uitstel and nu >= vandaag_start:
            gepland = morgen_start  # window already closed today
        state.gepland_voor = gepland
        reden = f"gepland ({reden_uitstel})" if uitstel else "gepland"
        return PlanResultaat(False, gepland, reden)

    state.gepland_voor = None
    return PlanResultaat(False, None, "niet nodig")


def start_nu(state: LegionellaState) -> None:
    """Manual start (button/service): force immediately."""
    state.forceer_actief = True
    state.gepland_voor = None
