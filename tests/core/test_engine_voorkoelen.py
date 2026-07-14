"""Engine wiring of the voorkoelen (pre-cool) channel."""

from datetime import datetime, timedelta

from custom_components.energie_manager.core.engine import beslis
from custom_components.energie_manager.core.model import (
    Config,
    Doel,
    EngineState,
    Invoer,
    LegionellaState,
    Modus,
)

T0 = datetime(2026, 7, 12, 12, 0)
GISTEREN = T0 - timedelta(days=1)


def invoer(**kw) -> Invoer:
    """Hot sunny afternoon: boiler done, battery full, 5 kW surplus."""
    basis = dict(
        pv_w=6000.0,
        ac_load_w=1000.0,
        batterij_w=0.0,
        soc=96.0,
        boiler_temp=61.0,
        ev_status="disconnected",
        ev_power_w=0.0,
        tarief=0.20,
        zon_vandaag_kwh=20.0,
        zon_morgen_kwh=20.0,
        binnen_temp=23.0,
        buiten_temp=26.0,
        dauwpunt_marge_c=3.5,
    )
    basis.update(kw)
    return Invoer(**basis)


def cfg(**kw) -> Config:
    kw.setdefault("voorkoelen_aan", True)
    return Config(**kw)


def state(**kw) -> EngineState:
    s = EngineState(**kw)
    s.legionella = LegionellaState(laatste_succes=GISTEREN)
    return s


def cmd_waarde(besluit, doel):
    for c in besluit.commandos:
        if c.doel is doel:
            return c.waarde
    return None


def test_start_bij_overschot_en_volle_accu():
    b, s = beslis(invoer(), cfg(), state(), T0)
    assert b.modus is Modus.VOORKOELEN
    assert b.voorkoelen_actief
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == -3.0
    assert s.voorkoelen_dwell_tot == T0 + timedelta(seconds=1800)
    # own dwell only: the global dwell stays untouched
    assert s.dwell_tot is None


def test_feature_uit_geeft_herstel():
    b, _ = beslis(invoer(), Config(voorkoelen_aan=False), state(), T0)
    assert not b.voorkoelen_actief
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_warmwater_reserveert_eerst():
    # boiler cold: warmwater claims 3 kW; 5-3=2 kW < 4 kW drempel
    b, _ = beslis(invoer(boiler_temp=45.0), cfg(), state(), T0)
    assert b.modus is Modus.WARMWATER_BOOST
    assert not b.voorkoelen_actief
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_seizoenscheck_buitentemperatuur():
    b, _ = beslis(invoer(buiten_temp=15.0), cfg(), state(), T0)
    assert not b.voorkoelen_actief


def test_accu_niet_vol_geen_voorkoelen():
    b, _ = beslis(invoer(soc=90.0), cfg(), state(), T0)
    assert not b.voorkoelen_actief


def test_comfortvloer_stopt_direct():
    s = state(voorkoelen_actief=True, voorkoelen_dwell_tot=T0 + timedelta(hours=1))
    b, s2 = beslis(invoer(binnen_temp=20.9), cfg(), s, T0)
    assert not b.voorkoelen_actief  # exempt: dwell does not hold it
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_temperatuur_onbekend_stopt_direct():
    s = state(voorkoelen_actief=True)
    b, _ = beslis(invoer(binnen_temp=None), cfg(), s, T0)
    assert not b.voorkoelen_actief


def test_temperatuur_onbekend_start_niet():
    b, _ = beslis(invoer(binnen_temp=None), cfg(), state(), T0)
    assert not b.voorkoelen_actief


def test_laag_overschot_timer():
    s = state(voorkoelen_actief=True)
    dun = invoer(pv_w=1500.0)  # overschot 0.5 kW < 1.5
    b, s = beslis(dun, cfg(), s, T0)
    assert b.voorkoelen_actief  # timer loopt nog
    b, s = beslis(dun, cfg(), s, T0 + timedelta(seconds=901))
    assert not b.voorkoelen_actief


def test_soc_daling_timer():
    s = state(voorkoelen_actief=True)
    laag = invoer(soc=85.0)
    b, s = beslis(laag, cfg(), s, T0)
    assert b.voorkoelen_actief
    b, s = beslis(laag, cfg(), s, T0 + timedelta(seconds=301))
    assert not b.voorkoelen_actief


def test_eigen_dwell_blokkeert_herstart():
    s = state(voorkoelen_actief=False, voorkoelen_dwell_tot=T0 + timedelta(seconds=900))
    b, _ = beslis(invoer(), cfg(), s, T0)
    assert not b.voorkoelen_actief  # wacht op eigen dwell
    b, _ = beslis(invoer(), cfg(), s, T0 + timedelta(seconds=901))
    assert b.voorkoelen_actief


def test_geforceerde_modus_voorkoelen():
    s = state(geforceerde_modus=Modus.VOORKOELEN, geforceerd_tot=T0 + timedelta(minutes=30))
    b, _ = beslis(invoer(binnen_temp=None), cfg(), s, T0)
    assert b.modus is Modus.VOORKOELEN
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == -3.0


def test_veilige_terugval_herstelt_offset():
    s = state(voorkoelen_actief=True)
    b, s2 = beslis(invoer(pv_w=None), cfg(), s, T0)
    assert b.modus is Modus.VEILIGE_TERUGVAL
    assert not s2.voorkoelen_actief
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_noodreserve_herstelt_offset():
    s = state(voorkoelen_actief=True)
    b, s2 = beslis(invoer(soc=9.0), cfg(), s, T0)
    assert b.modus is Modus.NOODRESERVE
    assert not s2.voorkoelen_actief
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_dauwpunt_marge_blokkeert_start():
    b, _ = beslis(invoer(dauwpunt_marge_c=1.5), cfg(), state(), T0)
    assert not b.voorkoelen_actief


def test_dauwpunt_marge_onbekend_blokkeert_start():
    b, _ = beslis(invoer(dauwpunt_marge_c=None), cfg(), state(), T0)
    assert not b.voorkoelen_actief


def test_dauwpunt_marge_stopt_direct():
    s = state(voorkoelen_actief=True, voorkoelen_dwell_tot=T0 + timedelta(hours=1))
    b, _ = beslis(invoer(dauwpunt_marge_c=0.8), cfg(), s, T0)
    assert not b.voorkoelen_actief  # exempt: condensation guard beats dwell
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0


def test_dauwpunt_marge_hysterese_houdt_aan():
    # between stop (min-1 = 1.0) and start (2.0): an active channel keeps going
    s = state(voorkoelen_actief=True)
    b, _ = beslis(invoer(dauwpunt_marge_c=1.5), cfg(), s, T0)
    assert b.voorkoelen_actief


def test_dauwpunt_bewaking_uitschakelbaar():
    b, _ = beslis(
        invoer(dauwpunt_marge_c=None), cfg(dauwpunt_marge_min_c=0.0), state(), T0
    )
    assert b.voorkoelen_actief  # guard disabled: channel works without sensor


def test_voorkoelen_dwell_blokkeert_warmwater_niet():
    # voorkoelen just switched (own dwell running); a warmwater start moments
    # later must not be blocked by it
    b, s = beslis(invoer(), cfg(), state(), T0)
    assert b.voorkoelen_actief
    b2, s2 = beslis(
        invoer(boiler_temp=45.0), cfg(), s, T0 + timedelta(seconds=30)
    )
    assert b2.warmwater_actief
