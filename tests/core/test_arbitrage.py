"""Tests for the peak-arbitrage planner (pure core)."""

from datetime import datetime

from custom_components.energie_manager.core.arbitrage import plan_arbitrage
from custom_components.energie_manager.core.model import (
    ArbitrageActie,
    Config,
    EngineState,
    Invoer,
    PrijsSlot,
)

T0 = datetime(2026, 7, 12, 12, 0)


def slot(uur: int, tarief: float, groep: str | None = None) -> PrijsSlot:
    return PrijsSlot(start=T0.replace(hour=uur), tarief=tarief, groep=groep)


def piek_slots(tarief_nu: float = 0.10) -> tuple[PrijsSlot, ...]:
    """Cheap-ish afternoon, expensive 18:00-20:00 peak, cheap late evening."""
    return (
        slot(12, tarief_nu),
        slot(13, 0.15),
        slot(14, 0.18),
        slot(15, 0.20),
        slot(16, 0.22),
        slot(17, 0.28),
        slot(18, 0.50, "expensive"),
        slot(19, 0.50, "expensive"),
        slot(20, 0.20),
        slot(21, 0.05),
    )


def maak_invoer(**kw) -> Invoer:
    basis = dict(
        soc=40.0,
        tarief=0.10,
        prijs_slots=piek_slots(),
        zon_vandaag_kwh=0.0,
    )
    basis.update(kw)
    return Invoer(**basis)


def plan(invoer=None, cfg=None, s=None, nu=T0, huislast=1.0):
    return plan_arbitrage(
        invoer if invoer is not None else maak_invoer(),
        cfg or Config(),
        s or EngineState(),
        nu,
        huislast,
    )


def test_geen_forecast():
    p = plan(maak_invoer(prijs_slots=()))
    assert p.actie is ArbitrageActie.GEEN
    assert "geen prijsvooruitzicht" in p.reden


def test_geen_piek_in_vooruitzicht():
    vlak = tuple(slot(u, 0.20) for u in range(12, 20))
    p = plan(maak_invoer(prijs_slots=vlak))
    assert p.actie is ArbitrageActie.GEEN
    assert p.piek_start is None


def test_negatieve_prijs_overlay_wint():
    p = plan(s=EngineState(negatieve_prijs_actief=True))
    assert p.actie is ArbitrageActie.GEEN
    assert "negatieve prijs" in p.reden


def test_voorladen_in_goedkoop_slot():
    # SoC 40, no sun: at 18:00 the battery is at ~30% -> nothing usable
    # above the 35% peak floor; the 12:00 slot is the cheapest pre-peak slot.
    p = plan()
    assert p.actie is ArbitrageActie.VOORLADEN
    assert p.piek_start == T0.replace(hour=18)
    assert p.tekort_kwh > 0
    assert T0.replace(hour=12) in p.laad_slots


def test_voorladen_respecteert_doel_soc():
    p = plan(maak_invoer(soc=96.0, zon_vandaag_kwh=50.0))
    assert p.actie is ArbitrageActie.GEEN  # battery covers the peak


def test_vasthouden_buiten_laadslot():
    # current hour is NOT among the cheapest slots -> hold instead
    slots = piek_slots(tarief_nu=0.25)
    inv = maak_invoer(prijs_slots=slots, tarief=0.25)
    p = plan(inv)
    assert p.actie is ArbitrageActie.VASTHOUDEN
    assert "vasthouden" in p.reden


def test_marge_gate_blokkeert_dunne_spread():
    # peak barely above the current price: not worth acting
    slots = (
        slot(12, 0.30),
        slot(13, 0.30),
        slot(14, 0.35, "expensive"),
        slot(15, 0.35, "expensive"),
    )
    p = plan(maak_invoer(prijs_slots=slots, tarief=0.30))
    assert p.actie is ArbitrageActie.GEEN


def test_hysterese_houdt_actie_vast():
    # battery ~covers the peak (tekort inside the ±0.9 kWh band)
    inv = maak_invoer(soc=41.0, tarief=0.25, prijs_slots=piek_slots(0.25))
    vers = plan(inv, huislast=0.55)
    was = plan(inv, s=EngineState(piek_vasthouden_actief=True), huislast=0.55)
    # tekort ends up between -0.9 and +0.9: fresh state does nothing,
    # an already-holding state keeps holding
    assert abs(vers.tekort_kwh) < 0.9
    assert vers.actie is ArbitrageActie.GEEN
    assert was.actie is ArbitrageActie.VASTHOUDEN


def test_ontladen_in_piek():
    nu = T0.replace(hour=18, minute=30)
    p = plan(maak_invoer(soc=90.0, tarief=0.50), nu=nu)
    assert p.actie is ArbitrageActie.ONTLADEN
    assert p.export_w == 5000  # capped by max_export_w / rails
    assert "teruglevering" in p.reden


def test_ontladen_export_bodem():
    nu = T0.replace(hour=18, minute=30)
    p = plan(maak_invoer(soc=90.0, tarief=0.25), nu=nu)
    assert p.actie is ArbitrageActie.GEEN


def test_ontladen_niet_onder_reserve():
    nu = T0.replace(hour=18, minute=30)
    # 36% SoC: usable above the 35% floor is ~0.6 kWh < house need
    p = plan(maak_invoer(soc=36.0, tarief=0.50), nu=nu)
    assert p.actie is ArbitrageActie.GEEN


def test_ontladen_herlaadkost_gate():
    # no cheap hours after the peak and no solar refill -> exporting means
    # buying back expensively: don't
    slots = (
        slot(18, 0.50, "expensive"),
        slot(19, 0.50, "expensive"),
        slot(20, 0.48),
        slot(21, 0.48),
    )
    nu = T0.replace(hour=18, minute=30)
    p = plan(maak_invoer(soc=90.0, tarief=0.50, prijs_slots=slots), nu=nu)
    assert p.actie is ArbitrageActie.GEEN


def test_verwacht_vol_om_met_veel_zon():
    p = plan(maak_invoer(soc=80.0, zon_vandaag_kwh=60.0))
    assert p.verwacht_vol_om is not None
