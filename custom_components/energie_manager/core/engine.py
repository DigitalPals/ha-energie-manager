"""The arbitration engine: one pure decision per tick.

``beslis()`` receives a snapshot of the world, the tunables and the engine's
memory, and returns the COMPLETE desired actuator state plus a Dutch
explanation. It never performs I/O and never reads the clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import ev as ev_mod
from . import legionella as leg_mod
from .model import (
    Besluit,
    Commando,
    Config,
    Doel,
    EngineState,
    Invoer,
    Modus,
    Overlay,
    kopieer_state,
)
from .prijs import nu_goedkoop

# Inputs without which no sensible decision is possible.
_KRITIEKE_INVOER = ("pv_w", "ac_load_w", "batterij_w", "soc", "boiler_temp")
# With an external surplus sensor the power triad is not critical.
_KRITIEKE_INVOER_EXTERN = ("soc", "boiler_temp")

_MAX_TICK_S = 120.0  # cap counter integration across gaps/restarts


def _overschot_kw(invoer: Invoer) -> float | None:
    """PV minus loads minus battery charging, in kW (surplus_after_battery)."""
    if invoer.overschot_extern_kw is not None:
        return invoer.overschot_extern_kw
    if invoer.pv_w is None or invoer.ac_load_w is None or invoer.batterij_w is None:
        return None
    return (invoer.pv_w - invoer.ac_load_w - max(0.0, invoer.batterij_w)) / 1000.0


def beslis(
    invoer: Invoer, config: Config, state: EngineState, nu: datetime
) -> tuple[Besluit, EngineState]:
    s = kopieer_state(state)
    tick_s = 0.0
    if s.laatste_tick is not None:
        tick_s = min((nu - s.laatste_tick).total_seconds(), _MAX_TICK_S)
    s.laatste_tick = nu

    overschot = _overschot_kw(invoer)
    redenen: list[str] = []

    # ------------------------------------------------------------------ #
    # 1. Legionella hold tracker (always runs; solar days self-satisfy).  #
    # ------------------------------------------------------------------ #
    if config.legionella_aan:
        hold = leg_mod.update_hold(s.legionella, invoer.boiler_temp, nu, config)
        if hold.succes:
            redenen.append("legionella-cyclus geslaagd (20 min ≥ doeltemperatuur)")
    else:
        hold = leg_mod.HoldResultaat(succes=False, bezig=False, hold_minuten=0.0)

    # ------------------------------------------------------------------ #
    # 2. Negative-price overlay debounce (independent of power inputs).   #
    # ------------------------------------------------------------------ #
    if config.negatieve_prijs_aan and invoer.tarief is not None:
        if invoer.tarief < 0:
            s.tarief_positief_sinds = None
            if s.tarief_negatief_sinds is None:
                s.tarief_negatief_sinds = nu
            if (
                not s.negatieve_prijs_actief
                and (nu - s.tarief_negatief_sinds).total_seconds()
                >= config.neg_prijs_vertraging_s
            ):
                s.negatieve_prijs_actief = True
        else:
            s.tarief_negatief_sinds = None
            if s.tarief_positief_sinds is None:
                s.tarief_positief_sinds = nu
            if (
                s.negatieve_prijs_actief
                and (nu - s.tarief_positief_sinds).total_seconds()
                >= config.neg_prijs_vertraging_s
            ):
                s.negatieve_prijs_actief = False
    elif not config.negatieve_prijs_aan:
        s.negatieve_prijs_actief = False
        s.tarief_negatief_sinds = None
        s.tarief_positief_sinds = None
    # tariff unavailable: hold current overlay state (safe).

    overlays = (
        frozenset({Overlay.NEGATIEVE_PRIJS})
        if s.negatieve_prijs_actief
        else frozenset()
    )
    if s.negatieve_prijs_actief:
        redenen.append("negatieve stroomprijs: teruglevering geblokkeerd")

    # ------------------------------------------------------------------ #
    # 3. Safety rung: critical inputs missing -> veilige_terugval.        #
    # ------------------------------------------------------------------ #
    kritiek = (
        _KRITIEKE_INVOER_EXTERN
        if invoer.overschot_extern_kw is not None
        else _KRITIEKE_INVOER
    )
    ontbrekend = [naam for naam in kritiek if getattr(invoer, naam) is None]
    if ontbrekend:
        return _veilige_terugval(invoer, config, s, nu, overlays, ontbrekend)

    soc: float = invoer.soc  # type: ignore[assignment]
    boiler: float = invoer.boiler_temp  # type: ignore[assignment]
    assert overschot is not None

    # ------------------------------------------------------------------ #
    # 4. Safety rung: emergency reserve.                                  #
    # ------------------------------------------------------------------ #
    if soc <= config.noodreserve_soc:
        return _noodreserve(invoer, config, s, nu, overlays, soc)

    # ------------------------------------------------------------------ #
    # 5. Legionella planning (may force the boost relay).                 #
    # ------------------------------------------------------------------ #
    if config.legionella_aan:
        plan_res = leg_mod.plan(
            s.legionella,
            nu,
            invoer.prijs_slots,
            invoer.zon_vandaag_kwh,
            invoer.zon_morgen_kwh,
            config,
        )
    else:
        plan_res = leg_mod.PlanResultaat(False, None, "legionellabewaking uit")

    boiler_klaar = boiler >= config.boiler_doel_c - 0.1

    # ------------------------------------------------------------------ #
    # 6. Desired channel activity (before dwell gating).                  #
    # ------------------------------------------------------------------ #
    # --- warmwater ---
    warmwater_gewenst = s.warmwater_actief
    warmwater_exempt = False  # transition exempt from dwell
    warmwater_reden = ""
    if plan_res.forceer and not boiler_klaar:
        warmwater_gewenst = True
        warmwater_exempt = True
        warmwater_reden = f"legionella: {plan_res.reden}"
    elif (
        config.warmwater_goedkoop_aan
        and not boiler_klaar
        and nu_goedkoop(invoer.prijs_slots, nu, invoer.tarief, config.prijsplafond_warmwater)
    ):
        warmwater_gewenst = True
        warmwater_reden = "warmwater op goedkope stroom"
    elif config.warmwater_aan:
        if not s.warmwater_actief:
            if (
                overschot >= config.overschot_drempel_kw
                and not boiler_klaar
                and (
                    soc >= config.batterij_prioriteit_soc
                    or boiler < config.boiler_comfortvloer_c
                )
            ):
                warmwater_gewenst = True
                warmwater_reden = (
                    f"overschot {overschot:.1f} kW ≥ {config.overschot_drempel_kw:.1f}, "
                    f"boiler {boiler:.1f}°"
                )
        else:
            warmwater_gewenst = True
            # off-conditions with their hysteresis timers
            if boiler_klaar:
                warmwater_gewenst = False
                warmwater_exempt = True
                warmwater_reden = f"boiler op doeltemperatuur ({boiler:.1f}°)"
            else:
                if overschot < config.uitschakel_drempel_kw:
                    if s.overschot_laag_sinds is None:
                        s.overschot_laag_sinds = nu
                    elif (
                        nu - s.overschot_laag_sinds
                    ).total_seconds() >= config.uitschakel_vertraging_s:
                        warmwater_gewenst = False
                        warmwater_reden = (
                            f"overschot {overschot:.1f} kW < "
                            f"{config.uitschakel_drempel_kw:.1f} (10 min)"
                        )
                else:
                    s.overschot_laag_sinds = None
                if (
                    soc < config.warmwater_soc_uitschakel
                    and boiler > config.boiler_comfortvloer_c
                ):
                    if s.soc_laag_sinds is None:
                        s.soc_laag_sinds = nu
                    elif (
                        nu - s.soc_laag_sinds
                    ).total_seconds() >= config.warmwater_soc_vertraging_s:
                        warmwater_gewenst = False
                        warmwater_reden = f"accu {soc:.0f}% heeft voorrang"
                else:
                    s.soc_laag_sinds = None
    else:
        warmwater_gewenst = False
        warmwater_exempt = True  # feature switched off: release promptly
        warmwater_reden = "warmwaterbeheer uit"
    # A legionella cycle in progress must never be interrupted by the
    # ordinary off-conditions (mirrors the old automation's guard).
    if s.legionella.forceer_actief and not boiler_klaar and config.legionella_aan:
        warmwater_gewenst = True
    if not warmwater_gewenst:
        s.overschot_laag_sinds = None
        s.soc_laag_sinds = None

    # --- EV ---
    ev_gewenst = s.ev_actief
    ev_exempt = False
    ev_ampere = s.ev_ampere
    ev_reden = ""
    status = invoer.ev_status
    ev_kw = (invoer.ev_power_w or 0.0) / 1000.0
    # manual "direct laden" override: charge until full or unplugged
    if s.ev_direct_laden and status in ev_mod.KLAAR_STATUSSEN:
        s.ev_direct_laden = False  # session over: back to automatic
        redenen.append(
            "EV direct laden beëindigd: "
            + ("auto vol" if status == "charged" else "losgekoppeld")
        )
    if s.ev_direct_laden and status in ev_mod.VERBONDEN_STATUSSEN:
        overschot_voor_ev = overschot - (
            config.overschot_drempel_kw if warmwater_gewenst else 0.0
        )
        amps_zon = ev_mod.zon_ampere(overschot_voor_ev, ev_kw, config)
        ev_gewenst = True
        ev_exempt = True  # manual action: no dwell wait
        ev_ampere = min(
            max(config.ev_vaste_ampere, amps_zon, config.ev_min_a), config.ev_max_a
        )
        ev_reden = f"handmatig: direct laden {ev_ampere} A"
    elif status is None:
        ev_gewenst = False
        ev_exempt = True
        ev_reden = "EV-status onbekend"
    elif status in ev_mod.KLAAR_STATUSSEN:
        ev_gewenst = False
        ev_exempt = True
        ev_reden = {
            "disconnected": "EV niet aangesloten",
            "charged": "EV vol",
            "low_soc": "EV: lage accu-status lader",
        }.get(status, status)
    elif status not in ev_mod.VERBONDEN_STATUSSEN:
        ev_gewenst = False
        ev_exempt = True
        ev_reden = f"EV-lader status {status}"
    else:
        goedkoop_ev = config.ev_goedkoop_aan and nu_goedkoop(
            invoer.prijs_slots, nu, invoer.tarief, config.prijsplafond_ev
        )
        # hot water reserves its power before the EV sees surplus
        overschot_voor_ev = overschot - (
            config.overschot_drempel_kw if warmwater_gewenst else 0.0
        )
        amps_zon = (
            ev_mod.zon_ampere(overschot_voor_ev, ev_kw, config)
            if config.ev_zon_aan
            else 0
        )
        if goedkoop_ev:
            ev_gewenst = True
            ev_ampere = max(config.ev_vaste_ampere, amps_zon)
            ev_ampere = min(max(ev_ampere, config.ev_min_a), config.ev_max_a)
            ev_reden = "EV laden op goedkope stroom"
        elif config.ev_zon_aan:
            if soc < config.batterij_reserve_soc:
                ev_gewenst = False
                ev_exempt = True  # battery protection: immediate
                ev_reden = f"accu {soc:.0f}% onder reserve"
            elif not s.ev_actief:
                if amps_zon >= config.ev_min_a and soc >= config.ev_start_soc:
                    ev_gewenst = True
                    ev_ampere = amps_zon
                    ev_reden = f"zonneladen {amps_zon} A uit overschot"
                else:
                    ev_gewenst = False
            else:
                beschikbaar = ev_mod.beschikbaar_kw(overschot_voor_ev, ev_kw)
                if amps_zon >= config.ev_min_a:
                    ev_ampere = amps_zon
                    ev_reden = f"zonneladen {amps_zon} A uit overschot"
                elif beschikbaar < config.ev_stop_kw:
                    ev_gewenst = False
                    ev_reden = (
                        f"te weinig overschot ({beschikbaar:.1f} kW < "
                        f"{config.ev_stop_kw:.1f})"
                    )
                else:
                    # dead zone: keep charging at the last commanded current
                    ev_ampere = max(s.ev_ampere, config.ev_min_a)
                    ev_reden = f"zonneladen aangehouden op {ev_ampere} A"
        else:
            ev_gewenst = False
            ev_reden = "EV-beheer uit"

    # --- battery grid-charge (goedkoop laden) ---
    if s.netladen_datum != nu.date().isoformat():
        s.netladen_datum = nu.date().isoformat()
        s.netladen_uren_vandaag = 0.0
    zon_bekend = (
        invoer.zon_vandaag_kwh is not None and invoer.zon_morgen_kwh is not None
    )
    zon_slecht = (
        zon_bekend
        and (invoer.zon_vandaag_kwh + invoer.zon_morgen_kwh)  # type: ignore[operator]
        < config.zon_slecht_drempel_kwh
    )
    netladen_gewenst = (
        config.netladen_aan
        and soc < config.doel_soc_netladen
        and zon_slecht
        and s.netladen_uren_vandaag < config.max_netladen_uren_per_dag
        and nu_goedkoop(invoer.prijs_slots, nu, invoer.tarief, config.prijsplafond_batterij)
    )
    netladen_reden = (
        f"accu netladen: goedkoop uur, accu {soc:.0f}% < {config.doel_soc_netladen:.0f}%"
        if netladen_gewenst
        else ""
    )

    # ------------------------------------------------------------------ #
    # 7. Forced mode (service override); safety rungs already returned.   #
    # ------------------------------------------------------------------ #
    geforceerd = False
    if s.geforceerde_modus is not None:
        if s.geforceerd_tot is not None and nu >= s.geforceerd_tot:
            s.geforceerde_modus = None
            s.geforceerd_tot = None
        else:
            geforceerd = True
            forced = s.geforceerde_modus
            warmwater_gewenst = forced is Modus.WARMWATER_BOOST
            ev_gewenst = forced is Modus.EV_LADEN
            if ev_gewenst:
                ev_ampere = config.ev_max_a
            netladen_gewenst = forced is Modus.GOEDKOOP_LADEN
            warmwater_exempt = ev_exempt = True
            redenen.append(f"handmatig geforceerd: {forced}")

    # ------------------------------------------------------------------ #
    # 8. Dwell gating on on/off transitions.                              #
    # ------------------------------------------------------------------ #
    mag_wisselen = s.dwell_tot is None or nu >= s.dwell_tot or geforceerd
    gewisseld = False

    def _gate(huidig: bool, gewenst: bool, exempt: bool) -> tuple[bool, bool]:
        """Return (result, changed) honoring the dwell timer."""
        nonlocal gewisseld
        if gewenst == huidig:
            return huidig, False
        if exempt or mag_wisselen:
            gewisseld = True
            return gewenst, True
        return huidig, False

    s.warmwater_actief, ww_wissel = _gate(
        s.warmwater_actief, warmwater_gewenst, warmwater_exempt
    )
    ev_was = s.ev_actief
    s.ev_actief, ev_wissel = _gate(s.ev_actief, ev_gewenst, ev_exempt)
    s.netladen_actief, net_wissel = _gate(
        s.netladen_actief, netladen_gewenst, exempt=False
    )
    if not ww_wissel and s.warmwater_actief != warmwater_gewenst:
        redenen.append("warmwater-wissel wacht op minimale modusduur")
    if not ev_wissel and s.ev_actief != ev_gewenst:
        redenen.append("EV-wissel wacht op minimale modusduur")
    if s.ev_actief:
        s.ev_ampere = ev_ampere
    elif ev_was and not s.ev_actief:
        s.ev_ampere = 0

    if gewisseld:
        s.dwell_tot = nu + timedelta(seconds=config.dwell_s)

    if s.netladen_actief and tick_s > 0:
        s.netladen_uren_vandaag += tick_s / 3600.0

    # ------------------------------------------------------------------ #
    # 9. Mode label + commands (complete desired state).                  #
    # ------------------------------------------------------------------ #
    if s.netladen_actief:
        modus = Modus.GOEDKOOP_LADEN
        if netladen_reden:
            redenen.insert(0, netladen_reden)
    elif s.warmwater_actief:
        modus = Modus.WARMWATER_BOOST
        if warmwater_reden:
            redenen.insert(0, warmwater_reden)
    elif s.ev_actief:
        modus = Modus.EV_LADEN
        if ev_reden:
            redenen.insert(0, ev_reden)
    elif soc <= config.batterij_reserve_soc:
        modus = Modus.BATTERIJ_BESCHERMEN
        redenen.insert(0, f"accu {soc:.0f}% op of onder reserve ({config.batterij_reserve_soc:.0f}%)")
    else:
        modus = Modus.ZELFVERBRUIK
        redenen.insert(0, _zelfverbruik_reden(overschot, warmwater_reden, ev_reden))
    if s.ev_actief and modus is not Modus.EV_LADEN and ev_reden:
        redenen.append(ev_reden)
    if s.warmwater_actief and modus is not Modus.WARMWATER_BOOST and warmwater_reden:
        redenen.append(warmwater_reden)

    if modus is not s.actieve_modus:
        s.actieve_modus = modus
        s.modus_sinds = nu

    commandos = _commandos(config, s, soc, overschot, overlays)

    besluit = Besluit(
        modus=modus,
        overlays=overlays,
        reden="; ".join(r for r in redenen if r),
        commandos=commandos,
        warmwater_actief=s.warmwater_actief,
        ev_actief=s.ev_actief,
        ev_ampere=s.ev_ampere,
        netladen_actief=s.netladen_actief,
        legionella_bezig=hold.bezig or s.legionella.forceer_actief,
        legionella_hold_minuten=hold.hold_minuten,
        overschot_kw=overschot,
    )
    return besluit, s


def _zelfverbruik_reden(overschot: float, ww_reden: str, ev_reden: str) -> str:
    delen = [f"zelfverbruik (overschot {overschot:.1f} kW)"]
    if ww_reden:
        delen.append(ww_reden)
    if ev_reden:
        delen.append(ev_reden)
    return ", ".join(delen)


def _commandos(
    config: Config,
    s: EngineState,
    soc: float | None,
    overschot: float | None,
    overlays: frozenset[Overlay],
) -> tuple[Commando, ...]:
    """Compose the complete desired actuator state for this tick."""
    neg = Overlay.NEGATIEVE_PRIJS in overlays

    # discharge rail: block battery discharge whenever we are deliberately
    # pulling from the grid (cheap charging / grid soak) or protecting it.
    grid_soak = s.netladen_actief or (
        (s.warmwater_actief or s.ev_actief)
        and overschot is not None
        and overschot < config.uitschakel_drempel_kw
    )
    beschermen = soc is not None and soc <= config.batterij_reserve_soc
    ontlading = 0.0 if (neg or grid_soak or beschermen) else config.ontlading_herstel_w

    feed_in = 0.0 if neg else config.feed_in_herstel_w
    curtail = neg and soc is not None and soc > config.neg_prijs_solar_limiet_soc
    limiet = 0.0 if curtail else 100.0
    setpoint = (
        config.max_laadvermogen_net_w if s.netladen_actief else config.setpoint_idle_w
    )

    cmds = [
        Commando(Doel.WARMWATER_RELAIS, s.warmwater_actief),
        Commando(Doel.EV_SCHAKELAAR, s.ev_actief),
        Commando(Doel.FEED_IN, feed_in),
        Commando(Doel.MAX_ONTLADING, ontlading),
        Commando(Doel.NET_SETPOINT, setpoint),
        Commando(Doel.SOLAR_LIMIET_1, limiet),
        Commando(Doel.SOLAR_LIMIET_2, limiet),
    ]
    if s.ev_actief and s.ev_ampere >= config.ev_min_a:
        cmds.insert(2, Commando(Doel.EV_STROOM, float(s.ev_ampere)))
    return tuple(cmds)


def _veilige_terugval(
    invoer: Invoer,
    config: Config,
    s: EngineState,
    nu: datetime,
    overlays: frozenset[Overlay],
    ontbrekend: list[str],
) -> tuple[Besluit, EngineState]:
    """Inputs unusable: release loads we own, preserve the battery."""
    cmds: list[Commando] = []
    if s.warmwater_actief:
        cmds.append(Commando(Doel.WARMWATER_RELAIS, False, "veilige terugval"))
        s.warmwater_actief = False
    if s.ev_actief:
        cmds.append(Commando(Doel.EV_SCHAKELAAR, False, "veilige terugval"))
        s.ev_actief = False
        s.ev_ampere = 0
    s.netladen_actief = False
    neg = Overlay.NEGATIEVE_PRIJS in overlays
    cmds += [
        Commando(Doel.FEED_IN, 0.0 if neg else config.feed_in_herstel_w),
        Commando(Doel.MAX_ONTLADING, 0.0),
        Commando(Doel.NET_SETPOINT, config.setpoint_idle_w),
        Commando(Doel.SOLAR_LIMIET_1, 100.0),
        Commando(Doel.SOLAR_LIMIET_2, 100.0),
    ]
    if s.actieve_modus is not Modus.VEILIGE_TERUGVAL:
        s.actieve_modus = Modus.VEILIGE_TERUGVAL
        s.modus_sinds = nu
    besluit = Besluit(
        modus=Modus.VEILIGE_TERUGVAL,
        overlays=overlays,
        reden="invoer ontbreekt of verouderd: " + ", ".join(ontbrekend),
        commandos=tuple(cmds),
    )
    return besluit, s


def _noodreserve(
    invoer: Invoer,
    config: Config,
    s: EngineState,
    nu: datetime,
    overlays: frozenset[Overlay],
    soc: float,
) -> tuple[Besluit, EngineState]:
    """Battery critically low: block discharge, drop every optional load."""
    cmds: list[Commando] = []
    if s.warmwater_actief:
        cmds.append(Commando(Doel.WARMWATER_RELAIS, False, "noodreserve"))
        s.warmwater_actief = False
    if s.ev_actief:
        cmds.append(Commando(Doel.EV_SCHAKELAAR, False, "noodreserve"))
        s.ev_actief = False
        s.ev_ampere = 0
    s.netladen_actief = False
    neg = Overlay.NEGATIEVE_PRIJS in overlays
    cmds += [
        Commando(Doel.FEED_IN, 0.0 if neg else config.feed_in_herstel_w),
        Commando(Doel.MAX_ONTLADING, 0.0),
        Commando(Doel.NET_SETPOINT, config.setpoint_idle_w),
        Commando(Doel.SOLAR_LIMIET_1, 100.0),
        Commando(Doel.SOLAR_LIMIET_2, 100.0),
    ]
    if s.actieve_modus is not Modus.NOODRESERVE:
        s.actieve_modus = Modus.NOODRESERVE
        s.modus_sinds = nu
    besluit = Besluit(
        modus=Modus.NOODRESERVE,
        overlays=overlays,
        reden=(
            f"accu {soc:.0f}% op of onder noodreserve "
            f"({config.noodreserve_soc:.0f}%): ontlading geblokkeerd"
        ),
        commandos=tuple(cmds),
    )
    return besluit, s
