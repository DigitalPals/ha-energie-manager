"""Dataclasses and enums shared by the decision core."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum


class Modus(StrEnum):
    """Primary mode: the headline of what the system is doing."""

    NOODRESERVE = "noodreserve"
    VEILIGE_TERUGVAL = "veilige_terugval"
    GOEDKOOP_LADEN = "goedkoop_laden"
    WARMWATER_BOOST = "warmwater_boost"
    EV_LADEN = "ev_laden"
    BATTERIJ_BESCHERMEN = "batterij_beschermen"
    ZELFVERBRUIK = "zelfverbruik"


# Ladder position: lower index = higher priority. Used for dwell preemption.
MODUS_PRIORITEIT: dict[Modus, int] = {m: i for i, m in enumerate(Modus)}


class Overlay(StrEnum):
    NEGATIEVE_PRIJS = "negatieve_prijs"


class Doel(StrEnum):
    """Symbolic actuator targets; the coordinator maps these to entity_ids."""

    WARMWATER_RELAIS = "warmwater_relais"
    EV_SCHAKELAAR = "ev_schakelaar"
    EV_STROOM = "ev_stroom"
    FEED_IN = "feed_in"
    MAX_ONTLADING = "max_ontlading"
    NET_SETPOINT = "net_setpoint"
    SOLAR_LIMIET_1 = "solar_limiet_1"
    SOLAR_LIMIET_2 = "solar_limiet_2"


@dataclass(frozen=True)
class Commando:
    doel: Doel
    waarde: float | bool
    reden: str = ""


@dataclass(frozen=True)
class PrijsSlot:
    """One hour of (forecast) electricity price."""

    start: datetime
    tarief: float  # €/kWh
    groep: str | None = None  # "cheap" / "normal" / "expensive" / None


@dataclass(frozen=True)
class SessieRecord:
    """Snapshot of a completed EV charging session (newest first in history)."""

    start: datetime
    einde: datetime
    energie_kwh: float
    energie_gratis_kwh: float  # solar/battery share, €0
    energie_net_kwh: float  # grid share, priced at the dynamic tariff
    energie_ongeprijsd_kwh: float  # grid share metered while tarief was None
    kosten_eur: float  # signed; negative tariffs yield negative cost


@dataclass
class SessieState:
    """Running EV charging session accumulator (see core.sessie)."""

    actief: bool = False
    start: datetime | None = None
    laatste_meter_kwh: float | None = None  # last seen charger session meter
    energie_kwh: float = 0.0
    energie_gratis_kwh: float = 0.0
    energie_net_kwh: float = 0.0
    energie_ongeprijsd_kwh: float = 0.0
    kosten_eur: float = 0.0


@dataclass(frozen=True)
class Invoer:
    """Snapshot of the world for one tick. None = unavailable or stale."""

    pv_w: float | None = None
    ac_load_w: float | None = None
    batterij_w: float | None = None  # positive = charging
    overschot_extern_kw: float | None = None  # optional override sensor
    soc: float | None = None
    boiler_temp: float | None = None
    ev_status: str | None = None  # decoded text, see core.ev
    ev_power_w: float | None = None
    ev_sessie_energie_kwh: float | None = None  # charger's own session meter
    net_vermogen_w: float | None = None  # grid power, positive = import
    tarief: float | None = None
    prijs_slots: tuple[PrijsSlot, ...] = ()
    zon_vandaag_kwh: float | None = None  # remaining forecast today
    zon_morgen_kwh: float | None = None
    # current actuator readback (for snapshots / diff context)
    relais_aan: bool | None = None
    ev_laden_aan: bool | None = None
    feed_in_w: float | None = None
    max_ontlading_w: float | None = None
    solar_limiet_pct: tuple[float | None, float | None] = (None, None)
    net_setpoint_w: float | None = None
    verouderd: tuple[str, ...] = ()  # names of stale inputs (informational)


@dataclass(frozen=True)
class Config:
    """Tunables. Mirrors the config entities; read fresh every tick."""

    # feature flags (master switch is handled outside the core)
    warmwater_aan: bool = True
    ev_zon_aan: bool = True
    legionella_aan: bool = True
    negatieve_prijs_aan: bool = True
    netladen_aan: bool = False
    warmwater_goedkoop_aan: bool = False
    ev_goedkoop_aan: bool = False

    # warmwater boost
    overschot_drempel_kw: float = 3.0  # also the reserved boost power
    uitschakel_drempel_kw: float = 1.5
    uitschakel_vertraging_s: float = 600.0
    boiler_doel_c: float = 61.0
    boiler_comfortvloer_c: float = 50.0
    batterij_prioriteit_soc: float = 95.0
    warmwater_soc_uitschakel: float = 90.0
    warmwater_soc_vertraging_s: float = 300.0

    # battery
    batterij_reserve_soc: float = 25.0
    noodreserve_soc: float = 10.0

    # EV
    ev_start_soc: float = 30.0  # reserve + 5: start hysteresis
    ev_min_a: int = 6
    ev_max_a: int = 32
    ev_w_per_a: float = 690.0  # 230 V x 3 phase
    ev_stop_kw: float = 3.5
    ev_vaste_ampere: int = 16  # for cheap-hour charging

    # price features
    prijsplafond_batterij: float = 0.0  # €/kWh; 0.0 = only free/negative hours
    prijsplafond_warmwater: float = 0.0
    prijsplafond_ev: float = 0.0
    doel_soc_netladen: float = 60.0
    max_laadvermogen_net_w: float = 2000.0
    max_netladen_uren_per_dag: float = 3.0
    zon_slecht_drempel_kwh: float = 10.0

    # negative price overlay
    neg_prijs_vertraging_s: float = 120.0
    neg_prijs_solar_limiet_soc: float = 94.0  # curtail PV only above this SoC

    # restore values
    feed_in_herstel_w: float = 5000.0
    ontlading_herstel_w: float = 5000.0
    setpoint_idle_w: float = 50.0

    # anti-flap
    dwell_s: float = 600.0

    # legionella
    legionella_interval_dagen: int = 7
    legionella_plan_dag: int = 6  # plan opportunistically from day 6
    legionella_hold_minuten: float = 20.0
    legionella_gap_minuten: float = 15.0
    legionella_venster_start_uur: int = 14
    legionella_venster_eind_uur: int = 20
    legionella_plan_minuut: int = 5


@dataclass
class LegionellaState:
    laatste_succes: datetime | None = None
    hold_start: datetime | None = None  # start of current >=61° streak
    hold_pauze_sinds: datetime | None = None  # temp dipped / data gap began
    forceer_actief: bool = False
    gepland_voor: datetime | None = None


@dataclass
class EngineState:
    """Mutable engine memory; a subset is persisted via Store."""

    actieve_modus: Modus = Modus.ZELFVERBRUIK
    modus_sinds: datetime | None = None
    dwell_tot: datetime | None = None

    warmwater_actief: bool = False
    ev_actief: bool = False
    ev_ampere: int = 0  # last commanded amps (kept in the 3.5 kW dead zone)
    ev_direct_laden: bool = False  # manual override: charge until full/unplugged
    netladen_actief: bool = False

    overschot_laag_sinds: datetime | None = None
    soc_laag_sinds: datetime | None = None

    tarief_negatief_sinds: datetime | None = None
    tarief_positief_sinds: datetime | None = None
    negatieve_prijs_actief: bool = False

    legionella: LegionellaState = field(default_factory=LegionellaState)

    sessie: SessieState = field(default_factory=SessieState)
    sessie_historie: list[SessieRecord] = field(default_factory=list)

    netladen_uren_vandaag: float = 0.0
    netladen_datum: str | None = None  # ISO date the counter belongs to

    geforceerde_modus: Modus | None = None
    geforceerd_tot: datetime | None = None

    laatste_tick: datetime | None = None


@dataclass(frozen=True)
class Besluit:
    """Complete desired state + explanation for one tick."""

    modus: Modus
    overlays: frozenset[Overlay]
    reden: str
    commandos: tuple[Commando, ...]
    # channel activity for entities/logbook
    warmwater_actief: bool = False
    ev_actief: bool = False
    ev_ampere: int = 0
    netladen_actief: bool = False
    legionella_bezig: bool = False
    legionella_hold_minuten: float = 0.0
    overschot_kw: float | None = None


def kopieer_state(state: EngineState) -> EngineState:
    """Copy the engine state (mutable sub-state copied too)."""
    nieuwe = replace(state)
    nieuwe.legionella = replace(state.legionella)
    nieuwe.sessie = replace(state.sessie)
    nieuwe.sessie_historie = list(state.sessie_historie)
    return nieuwe
