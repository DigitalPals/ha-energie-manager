"""Tests for the EV session accountant (core.sessie)."""

from datetime import datetime, timedelta

from custom_components.energie_manager.core import sessie
from custom_components.energie_manager.core.engine import beslis
from custom_components.energie_manager.core.model import (
    Config,
    EngineState,
    Invoer,
    LegionellaState,
    SessieRecord,
    SessieState,
    kopieer_state,
)

T0 = datetime(2026, 7, 12, 12, 0)

# Sensible charging-tick defaults: 10 kW EV draw, 5 kW grid import, €0.20/kWh.
BASIS = dict(
    ev_status="charging",
    ev_power_w=10_000.0,
    net_vermogen_w=5_000.0,
    tarief=0.20,
)


def tick(s, historie, meter, nu=T0, **over):
    kw = {**BASIS, **over}
    return sessie.update(
        s,
        historie,
        meter_kwh=meter,
        ev_status=kw["ev_status"],
        ev_power_w=kw["ev_power_w"],
        net_vermogen_w=kw["net_vermogen_w"],
        tarief=kw["tarief"],
        nu=nu,
    )


def lopende_sessie(meter=1.0, **kw) -> SessieState:
    basis = dict(actief=True, start=T0 - timedelta(hours=1), laatste_meter_kwh=meter)
    basis.update(kw)
    return SessieState(**basis)


def test_start_bij_eerste_delta():
    s = SessieState(laatste_meter_kwh=0.0)
    res = tick(s, [], 0.02)
    assert res.gestart
    assert s.actief
    assert s.start == T0
    assert abs(s.energie_kwh - 0.02) < 1e-9


def test_eerste_waarneming_baseline_zonder_sessie():
    # First meter sighting while disconnected: baseline only, no session.
    s = SessieState()
    res = tick(s, [], 3.5, ev_status="disconnected")
    assert not res.gestart and not s.actief
    assert s.laatste_meter_kwh == 3.5


def test_eerste_waarneming_adopteert_lopende_sessie():
    # Restart-with-empty-store while mid-charge: adopt the running session.
    s = SessieState()
    res = tick(s, [], 3.5, ev_status="charging")
    assert res.gestart and s.actief
    assert s.energie_kwh == 0.0  # baseline, no delta yet


def test_split_half_net():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0)  # 1 kWh delta; 5 kW of 10 kW from grid
    assert abs(s.energie_net_kwh - 0.5) < 1e-9
    assert abs(s.energie_gratis_kwh - 0.5) < 1e-9
    assert abs(s.kosten_eur - 0.1) < 1e-9  # 0.5 kWh x €0.20


def test_vol_zon_gratis():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0, net_vermogen_w=-3_000.0)  # exporting: everything free
    assert s.energie_net_kwh == 0.0
    assert abs(s.energie_gratis_kwh - 1.0) < 1e-9
    assert s.kosten_eur == 0.0


def test_net_groter_dan_ev_clamp():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0, net_vermogen_w=20_000.0)  # house imports more than EV draws
    assert abs(s.energie_net_kwh - 1.0) < 1e-9
    assert s.energie_gratis_kwh == 0.0


def test_negatief_tarief():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0, net_vermogen_w=20_000.0, tarief=-0.05)
    assert abs(s.kosten_eur - (-0.05)) < 1e-9


def test_tarief_onbekend_ongeprijsd():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0, tarief=None)
    assert abs(s.energie_net_kwh - 0.5) < 1e-9
    assert abs(s.energie_ongeprijsd_kwh - 0.5) < 1e-9
    assert s.kosten_eur == 0.0


def test_meter_reset_finaliseert_en_start_nieuw():
    historie = []
    s = lopende_sessie(meter=8.4, energie_kwh=8.4, energie_gratis_kwh=8.4)
    res = tick(s, historie, 0.01)
    assert res.beeindigd and res.gestart
    assert len(historie) == 1
    assert abs(historie[0].energie_kwh - 8.4) < 1e-9
    assert s.actief
    assert abs(s.energie_kwh - 0.01) < 1e-9  # fresh meter = first delta


def test_einde_op_charged_en_disconnected():
    for status in ("charged", "disconnected"):
        historie = []
        s = lopende_sessie(meter=5.0, energie_kwh=5.0, kosten_eur=0.6)
        res = tick(s, historie, 5.0, ev_status=status)
        assert res.beeindigd
        assert not s.actief and s.energie_kwh == 0.0
        assert len(historie) == 1
        assert historie[0].einde == T0
        assert abs(historie[0].kosten_eur - 0.6) < 1e-9
        # meter memory survives the reset for the next delta
        assert s.laatste_meter_kwh == 5.0


def test_historie_max_10_nieuwste_eerst():
    historie = []
    for i in range(12):
        s = lopende_sessie(meter=float(i), energie_kwh=float(i))
        tick(s, historie, float(i), ev_status="charged", nu=T0 + timedelta(hours=i))
    assert len(historie) == 10
    assert historie[0].einde == T0 + timedelta(hours=11)  # newest first


def test_meter_onbeschikbaar_geen_dubbeltelling():
    s = lopende_sessie(meter=1.0)
    tick(s, [], None)
    tick(s, [], None)
    tick(s, [], 2.0)  # gap delta lands once
    assert abs(s.energie_kwh - 1.0) < 1e-9


def test_ev_vermogen_stil_meter_stijgt():
    s = lopende_sessie(meter=1.0)
    # power sensor stale/zero, grid importing: everything priced
    tick(s, [], 1.1, ev_power_w=0.0, net_vermogen_w=2_000.0)
    assert abs(s.energie_net_kwh - 0.1) < 1e-9
    # power sensor stale/zero, no grid import: everything free
    tick(s, [], 1.2, ev_power_w=None, net_vermogen_w=0.0)
    assert abs(s.energie_gratis_kwh - 0.1) < 1e-9


def test_net_onbeschikbaar_conservatief():
    s = lopende_sessie(meter=1.0)
    tick(s, [], 2.0, net_vermogen_w=None)
    assert abs(s.energie_net_kwh - 1.0) < 1e-9  # unknown split: priced


def test_status_none_houdt_sessie():
    s = lopende_sessie(meter=1.0)
    res = tick(s, [], 2.0, ev_status=None)
    assert not res.beeindigd
    assert s.actief
    assert abs(s.energie_kwh - 1.0) < 1e-9


def test_kopieer_state_isoleert_sessie():
    s = EngineState()
    s.sessie = lopende_sessie(meter=1.0, energie_kwh=1.0)
    s.sessie_historie = [
        SessieRecord(
            start=T0,
            einde=T0,
            energie_kwh=1.0,
            energie_gratis_kwh=1.0,
            energie_net_kwh=0.0,
            energie_ongeprijsd_kwh=0.0,
            kosten_eur=0.0,
        )
    ]
    kopie = kopieer_state(s)
    kopie.sessie.energie_kwh = 99.0
    kopie.sessie_historie.clear()
    assert s.sessie.energie_kwh == 1.0
    assert len(s.sessie_historie) == 1


def test_beslis_sessie_loopt_in_veilige_terugval():
    # PV sensor stale -> veilige_terugval early-return, but accounting advances.
    s = EngineState()
    s.legionella = LegionellaState(laatste_succes=T0 - timedelta(days=1))
    s.sessie = lopende_sessie(meter=1.0)
    invoer = Invoer(
        pv_w=None,  # critical input missing
        ac_load_w=1000.0,
        batterij_w=0.0,
        soc=50.0,
        boiler_temp=45.0,
        ev_status="charging",
        ev_power_w=10_000.0,
        ev_sessie_energie_kwh=2.0,
        net_vermogen_w=5_000.0,
        tarief=0.20,
    )
    besluit, nieuw = beslis(invoer, Config(), s, T0)
    assert besluit.modus.value == "veilige_terugval"
    assert abs(nieuw.sessie.energie_kwh - 1.0) < 1e-9
    assert abs(nieuw.sessie.kosten_eur - 0.1) < 1e-9
