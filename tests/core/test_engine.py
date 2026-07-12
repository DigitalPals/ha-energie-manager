"""Table-driven tests for the arbitration engine."""

from datetime import datetime, timedelta

from custom_components.energie_manager.core.engine import beslis
from custom_components.energie_manager.core.model import (
    Config,
    Doel,
    EngineState,
    Invoer,
    LegionellaState,
    Modus,
    Overlay,
)

T0 = datetime(2026, 7, 12, 12, 0)
GISTEREN = T0 - timedelta(days=1)


def config(**kw) -> Config:
    return Config(**kw)


def invoer(**kw) -> Invoer:
    """A sunny, healthy afternoon by default."""
    basis = dict(
        pv_w=6000.0,
        ac_load_w=1000.0,
        batterij_w=0.0,
        soc=96.0,
        boiler_temp=45.0,
        ev_status="disconnected",
        ev_power_w=0.0,
        tarief=0.20,
        zon_vandaag_kwh=20.0,
        zon_morgen_kwh=20.0,
    )
    basis.update(kw)
    return Invoer(**basis)


def state(**kw) -> EngineState:
    s = EngineState(**kw)
    # a fresh success yesterday keeps the legionella planner quiet
    s.legionella = LegionellaState(laatste_succes=GISTEREN)
    return s


def cmd_waarde(besluit, doel):
    for c in besluit.commandos:
        if c.doel is doel:
            return c.waarde
    return None


# --------------------------------------------------------------------- #
# Default / ladder basics                                                #
# --------------------------------------------------------------------- #


def test_zelfverbruik_default():
    b, s = beslis(invoer(boiler_temp=61.0), config(), state(), T0)
    assert b.modus is Modus.ZELFVERBRUIK
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is False
    assert cmd_waarde(b, Doel.FEED_IN) == 5000.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 5000.0
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
    assert cmd_waarde(b, Doel.SOLAR_LIMIET_1) == 100.0


def test_noodreserve():
    b, s = beslis(invoer(soc=10.0), config(), state(warmwater_actief=True), T0)
    assert b.modus is Modus.NOODRESERVE
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is False


def test_batterij_beschermen_op_reserve():
    b, _ = beslis(invoer(soc=25.0, boiler_temp=61.0), config(), state(), T0)
    assert b.modus is Modus.BATTERIJ_BESCHERMEN
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    b, _ = beslis(invoer(soc=26.0, boiler_temp=61.0), config(), state(), T0)
    assert b.modus is Modus.ZELFVERBRUIK


def test_veilige_terugval_bij_ontbrekende_invoer():
    b, s = beslis(invoer(pv_w=None), config(), state(warmwater_actief=True), T0)
    assert b.modus is Modus.VEILIGE_TERUGVAL
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is False
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    assert not s.warmwater_actief


def test_veilige_terugval_raakt_niet_ons_relais():
    # relay physically on but not ours: no relay command in terugval
    b, _ = beslis(
        invoer(soc=None, relais_aan=True), config(), state(warmwater_actief=False), T0
    )
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is None


# --------------------------------------------------------------------- #
# Warmwater boost thresholds                                             #
# --------------------------------------------------------------------- #


def test_boost_aan_op_drempel():
    # surplus (6000-1000-0)/1000 = 5.0 kW, soc 96, boiler 45
    b, s = beslis(invoer(), config(), state(), T0)
    assert b.modus is Modus.WARMWATER_BOOST
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is True
    assert s.warmwater_actief


def test_boost_niet_onder_drempel():
    b, _ = beslis(invoer(pv_w=3900.0), config(), state(), T0)  # 2.9 kW
    assert b.modus is Modus.ZELFVERBRUIK


def test_boost_exact_30_kw():
    b, _ = beslis(invoer(pv_w=4000.0), config(), state(), T0)  # 3.0 kW
    assert b.modus is Modus.WARMWATER_BOOST


def test_boost_niet_als_boiler_klaar():
    b, _ = beslis(invoer(boiler_temp=60.9), config(), state(), T0)
    assert b.modus is Modus.ZELFVERBRUIK


def test_boost_batterijprioriteit():
    # soc 80 < 95 and boiler above comfort floor: battery first
    b, _ = beslis(invoer(soc=80.0, boiler_temp=55.0), config(), state(), T0)
    assert b.modus is Modus.ZELFVERBRUIK
    # boiler below comfort floor: boost anyway
    b, _ = beslis(invoer(soc=80.0, boiler_temp=45.0), config(), state(), T0)
    assert b.modus is Modus.WARMWATER_BOOST


def test_boost_uit_direct_op_doeltemperatuur():
    s = state(warmwater_actief=True)
    s.dwell_tot = T0 + timedelta(seconds=500)  # still within dwell: exempt
    b, s2 = beslis(invoer(boiler_temp=61.0), config(), s, T0)
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is False
    assert not s2.warmwater_actief


def test_boost_uit_na_10_min_laag_overschot():
    cfg = config()
    s = state(warmwater_actief=True)
    laag = invoer(pv_w=2000.0)  # 1.0 kW < 1.5
    b, s = beslis(laag, cfg, s, T0)
    assert s.warmwater_actief  # timer started, still on
    b, s = beslis(laag, cfg, s, T0 + timedelta(minutes=9))
    assert s.warmwater_actief
    b, s = beslis(laag, cfg, s, T0 + timedelta(minutes=10, seconds=1))
    assert not s.warmwater_actief


def test_boost_laag_overschot_timer_reset():
    cfg = config()
    s = state(warmwater_actief=True)
    b, s = beslis(invoer(pv_w=2000.0), cfg, s, T0)
    b, s = beslis(invoer(), cfg, s, T0 + timedelta(minutes=5))  # recovers
    b, s = beslis(invoer(pv_w=2000.0), cfg, s, T0 + timedelta(minutes=6))
    b, s = beslis(invoer(pv_w=2000.0), cfg, s, T0 + timedelta(minutes=15))
    assert s.warmwater_actief  # only 9 min since new timer start
    b, s = beslis(invoer(pv_w=2000.0), cfg, s, T0 + timedelta(minutes=16, seconds=5))
    assert not s.warmwater_actief


def test_boost_uit_na_5_min_lage_soc():
    cfg = config()
    s = state(warmwater_actief=True)
    laag = invoer(soc=89.0, boiler_temp=55.0)  # above comfort floor
    b, s = beslis(laag, cfg, s, T0)
    assert s.warmwater_actief
    b, s = beslis(laag, cfg, s, T0 + timedelta(minutes=5, seconds=1))
    assert not s.warmwater_actief


def test_boost_blijft_bij_lage_soc_onder_comfortvloer():
    cfg = config()
    s = state(warmwater_actief=True)
    laag = invoer(soc=89.0, boiler_temp=45.0)  # below comfort floor
    b, s = beslis(laag, cfg, s, T0)
    b, s = beslis(laag, cfg, s, T0 + timedelta(minutes=6))
    assert s.warmwater_actief


# --------------------------------------------------------------------- #
# EV zonneladen                                                          #
# --------------------------------------------------------------------- #


def test_ev_start_met_overschot():
    # boiler done -> no boost reservation; 5 kW surplus -> 7 A
    b, s = beslis(
        invoer(boiler_temp=61.0, ev_status="connected"), config(), state(), T0
    )
    assert b.modus is Modus.EV_LADEN
    assert cmd_waarde(b, Doel.EV_SCHAKELAAR) is True
    assert cmd_waarde(b, Doel.EV_STROOM) == 7.0
    assert b.ev_ampere == 7


def test_ev_wacht_op_warmwater_reservering():
    # 5 kW surplus but boost wants its 3 kW -> 2 kW for EV -> below 6 A
    b, _ = beslis(invoer(ev_status="connected"), config(), state(), T0)
    assert b.modus is Modus.WARMWATER_BOOST
    assert cmd_waarde(b, Doel.EV_SCHAKELAAR) is False


def test_ev_start_soc_hysterese():
    inv = invoer(boiler_temp=61.0, ev_status="connected", soc=29.0)
    b, _ = beslis(inv, config(), state(), T0)
    assert cmd_waarde(b, Doel.EV_SCHAKELAAR) is False  # start needs >= 30
    # already charging: 29% is fine (stop is < 25)
    s = state(ev_actief=True, ev_ampere=7)
    b, _ = beslis(inv, config(), s, T0)
    assert cmd_waarde(b, Doel.EV_SCHAKELAAR) is True


def test_ev_stop_onder_reserve_direct():
    s = state(ev_actief=True, ev_ampere=7)
    s.dwell_tot = T0 + timedelta(seconds=500)
    b, s2 = beslis(
        invoer(boiler_temp=61.0, ev_status="charging", soc=24.0), config(), s, T0
    )
    assert not s2.ev_actief  # battery protection is dwell-exempt


def test_ev_ampere_update_zonder_dwell():
    s = state(ev_actief=True, ev_ampere=7)
    s.dwell_tot = T0 + timedelta(seconds=500)  # within dwell
    b, s2 = beslis(
        invoer(boiler_temp=61.0, ev_status="charging", pv_w=11000.0, ev_power_w=4830.0),
        config(),
        s,
        T0,
    )
    # (11000-1000)/1000 + 4.83 = 14.83 kW -> 21 A, updated despite dwell
    assert cmd_waarde(b, Doel.EV_STROOM) == 21.0


def test_ev_dode_zone_houdt_stroom_vast():
    s = state(ev_actief=True, ev_ampere=6)
    # beschikbaar = 0.0 surplus + 3.8 EV = 3.8 kW: >= 3.5 stop maar < 6 A
    b, s2 = beslis(
        invoer(boiler_temp=61.0, ev_status="charging", pv_w=1000.0, ev_power_w=3800.0),
        config(),
        s,
        T0,
    )
    assert s2.ev_actief
    assert cmd_waarde(b, Doel.EV_STROOM) == 6.0


def test_ev_stop_onder_35_kw_na_dwell():
    cfg = config()
    s = state(ev_actief=True, ev_ampere=7)
    s.dwell_tot = T0 + timedelta(seconds=600)
    krap = invoer(boiler_temp=61.0, ev_status="charging", pv_w=1000.0, ev_power_w=3000.0)
    # beschikbaar = 0.0 + 3.0 = 3.0 < 3.5 -> stop gewenst, maar dwell blokkeert
    b, s = beslis(krap, cfg, s, T0)
    assert s.ev_actief
    b, s = beslis(krap, cfg, s, T0 + timedelta(seconds=601))
    assert not s.ev_actief


def test_ev_losgekoppeld_stopt_direct():
    s = state(ev_actief=True, ev_ampere=16)
    s.dwell_tot = T0 + timedelta(seconds=500)
    b, s2 = beslis(invoer(boiler_temp=61.0, ev_status="disconnected"), config(), s, T0)
    assert not s2.ev_actief
    assert cmd_waarde(b, Doel.EV_SCHAKELAAR) is False


# --------------------------------------------------------------------- #
# Negatieve prijs overlay                                                #
# --------------------------------------------------------------------- #


def test_negatieve_prijs_debounce():
    cfg = config()
    s = state()
    neg = invoer(boiler_temp=61.0, tarief=-0.01)
    b, s = beslis(neg, cfg, s, T0)
    assert Overlay.NEGATIEVE_PRIJS not in b.overlays
    b, s = beslis(neg, cfg, s, T0 + timedelta(seconds=60))
    assert Overlay.NEGATIEVE_PRIJS not in b.overlays
    b, s = beslis(neg, cfg, s, T0 + timedelta(seconds=121))
    assert Overlay.NEGATIEVE_PRIJS in b.overlays
    assert cmd_waarde(b, Doel.FEED_IN) == 0.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    assert cmd_waarde(b, Doel.SOLAR_LIMIET_1) == 0.0  # soc 96 > 94
    # restore needs 2 min positive
    pos = invoer(boiler_temp=61.0, tarief=0.05)
    b, s = beslis(pos, cfg, s, T0 + timedelta(seconds=180))
    assert Overlay.NEGATIEVE_PRIJS in b.overlays
    b, s = beslis(pos, cfg, s, T0 + timedelta(seconds=302))
    assert Overlay.NEGATIEVE_PRIJS not in b.overlays
    assert cmd_waarde(b, Doel.FEED_IN) == 5000.0
    assert cmd_waarde(b, Doel.SOLAR_LIMIET_1) == 100.0


def test_negatieve_prijs_geen_pv_limiet_bij_lage_soc():
    cfg = config()
    s = state()
    s.negatieve_prijs_actief = True
    b, _ = beslis(invoer(boiler_temp=61.0, soc=90.0, tarief=-0.05), cfg, s, T0)
    assert cmd_waarde(b, Doel.SOLAR_LIMIET_1) == 100.0
    assert cmd_waarde(b, Doel.FEED_IN) == 0.0


def test_negatieve_prijs_boost_blijft_mogelijk():
    """Soaking surplus into hot water during negative prices stays allowed."""
    cfg = config()
    s = state()
    s.negatieve_prijs_actief = True
    b, _ = beslis(invoer(tarief=-0.05), cfg, s, T0)
    assert b.modus is Modus.WARMWATER_BOOST
    assert Overlay.NEGATIEVE_PRIJS in b.overlays
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is True


# --------------------------------------------------------------------- #
# Goedkoop laden (netladen) + grid-soak varianten                        #
# --------------------------------------------------------------------- #


def _nacht_goedkoop(**kw):
    basis = dict(
        pv_w=0.0,
        ac_load_w=500.0,
        boiler_temp=61.0,
        soc=40.0,
        tarief=-0.02,
        zon_vandaag_kwh=2.0,
        zon_morgen_kwh=3.0,
    )
    basis.update(kw)
    return invoer(**basis)


def test_netladen_aan():
    cfg = config(netladen_aan=True)
    b, s = beslis(_nacht_goedkoop(), cfg, state(), T0)
    assert b.modus is Modus.GOEDKOOP_LADEN
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 2000.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0  # never discharge while grid-charging
    assert s.netladen_actief


def test_netladen_uit_zonder_flag():
    b, _ = beslis(_nacht_goedkoop(), config(), state(), T0)
    assert b.modus is not Modus.GOEDKOOP_LADEN
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0


def test_netladen_niet_bij_goede_zonverwachting():
    cfg = config(netladen_aan=True)
    b, _ = beslis(_nacht_goedkoop(zon_morgen_kwh=15.0), cfg, state(), T0)
    assert b.modus is not Modus.GOEDKOOP_LADEN


def test_netladen_niet_boven_doel_soc():
    cfg = config(netladen_aan=True)
    b, _ = beslis(_nacht_goedkoop(soc=60.0), cfg, state(), T0)
    assert b.modus is not Modus.GOEDKOOP_LADEN


def test_netladen_dagbudget():
    cfg = config(netladen_aan=True)
    s = state()
    s.netladen_datum = T0.date().isoformat()
    s.netladen_uren_vandaag = 3.0
    b, _ = beslis(_nacht_goedkoop(), cfg, s, T0)
    assert b.modus is not Modus.GOEDKOOP_LADEN


def test_netladen_urenteller_en_datumreset():
    cfg = config(netladen_aan=True)
    s = state()
    b, s = beslis(_nacht_goedkoop(), cfg, s, T0)
    b, s = beslis(_nacht_goedkoop(), cfg, s, T0 + timedelta(seconds=30))
    assert 0.008 < s.netladen_uren_vandaag < 0.009  # 30 s
    s.netladen_uren_vandaag = 2.5
    morgen = T0 + timedelta(days=1)
    b, s = beslis(_nacht_goedkoop(), cfg, s, morgen)
    assert s.netladen_datum == morgen.date().isoformat()
    assert s.netladen_uren_vandaag < 2.5  # reset


def test_warmwater_goedkoop():
    cfg = config(warmwater_goedkoop_aan=True)
    b, s = beslis(_nacht_goedkoop(boiler_temp=50.0), cfg, state(), T0)
    assert b.modus is Modus.WARMWATER_BOOST
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is True
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0  # grid soak: don't drain battery


def test_ev_goedkoop_vaste_ampere():
    cfg = config(ev_goedkoop_aan=True)
    b, s = beslis(
        _nacht_goedkoop(ev_status="connected", soc=40.0), cfg, state(), T0
    )
    assert b.modus is Modus.EV_LADEN
    assert cmd_waarde(b, Doel.EV_STROOM) == 16.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0


def test_ev_goedkoop_wint_van_zon():
    """When both apply: max(configured, surplus amps)."""
    cfg = config(ev_goedkoop_aan=True)
    # 14 kW surplus -> 20 A zon > 16 A vast
    b, _ = beslis(
        invoer(boiler_temp=61.0, ev_status="connected", pv_w=15000.0, tarief=-0.05),
        cfg,
        state(),
        T0,
    )
    assert cmd_waarde(b, Doel.EV_STROOM) == 20.0


# --------------------------------------------------------------------- #
# Dwell                                                                  #
# --------------------------------------------------------------------- #


def test_dwell_blokkeert_snelle_start():
    cfg = config()
    s = state()
    s.dwell_tot = T0 + timedelta(seconds=600)
    b, s2 = beslis(invoer(), cfg, s, T0 + timedelta(seconds=30))
    assert not s2.warmwater_actief  # wants on, but dwell blocks
    b, s3 = beslis(invoer(), cfg, s2, T0 + timedelta(seconds=601))
    assert s3.warmwater_actief


def test_dwell_gezet_na_wissel():
    b, s = beslis(invoer(), config(), state(), T0)
    assert s.warmwater_actief
    assert s.dwell_tot == T0 + timedelta(seconds=600)


# --------------------------------------------------------------------- #
# Legionella via de engine                                               #
# --------------------------------------------------------------------- #


def test_legionella_forceert_zonder_overschot():
    s = state()
    s.legionella = LegionellaState(laatste_succes=T0 - timedelta(days=8))
    nu = T0.replace(hour=15)
    b, s2 = beslis(invoer(pv_w=0.0, boiler_temp=50.0, soc=50.0), config(), s, nu)
    assert b.modus is Modus.WARMWATER_BOOST
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is True
    assert b.legionella_bezig


def test_legionella_avondstop_via_engine():
    s = state()
    s.legionella = LegionellaState(
        laatste_succes=T0 - timedelta(days=8), forceer_actief=True
    )
    s.warmwater_actief = True
    nu = T0.replace(hour=20, minute=0)
    b, s2 = beslis(invoer(pv_w=0.0, boiler_temp=55.0, soc=50.0), config(), s, nu)
    assert not s2.legionella.forceer_actief
    # relay released (low surplus timer starts; boost no longer forced)
    assert not s2.legionella.forceer_actief


def test_legionella_succes_via_engine():
    cfg = config()
    s = state()
    s.legionella = LegionellaState(laatste_succes=None, forceer_actief=True)
    s.warmwater_actief = True
    nu = T0.replace(hour=15)
    heet = invoer(pv_w=0.0, boiler_temp=61.5, soc=50.0)
    for minuut in range(0, 21):
        b, s = beslis(heet, cfg, s, nu + timedelta(minutes=minuut))
    assert s.legionella.laatste_succes is not None
    assert not s.legionella.forceer_actief


def test_legionella_veto_door_noodreserve():
    s = state()
    s.legionella = LegionellaState(laatste_succes=T0 - timedelta(days=8))
    nu = T0.replace(hour=15)
    b, _ = beslis(invoer(soc=9.0, boiler_temp=50.0), config(), s, nu)
    assert b.modus is Modus.NOODRESERVE
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is not True


# --------------------------------------------------------------------- #
# Geforceerde modus (service)                                            #
# --------------------------------------------------------------------- #


def test_geforceerde_modus_en_verloop():
    cfg = config()
    s = state()
    s.geforceerde_modus = Modus.WARMWATER_BOOST
    s.geforceerd_tot = T0 + timedelta(minutes=30)
    b, s = beslis(invoer(pv_w=0.0, boiler_temp=55.0), cfg, s, T0)
    assert cmd_waarde(b, Doel.WARMWATER_RELAIS) is True
    # expires
    b, s = beslis(invoer(pv_w=0.0, boiler_temp=55.0), cfg, s, T0 + timedelta(minutes=31))
    assert s.geforceerde_modus is None
