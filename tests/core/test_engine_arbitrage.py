"""Engine wiring of the arbitrage planner (hold / pre-charge / export)."""

from datetime import datetime, timedelta

from custom_components.energie_manager.core.engine import beslis
from custom_components.energie_manager.core.model import (
    ArbitrageActie,
    Config,
    Doel,
    EngineState,
    Invoer,
    LegionellaState,
    Modus,
    PrijsSlot,
)

T0 = datetime(2026, 7, 12, 12, 0)
GISTEREN = T0 - timedelta(days=1)


def slot(uur: int, tarief: float, groep: str | None = None) -> PrijsSlot:
    return PrijsSlot(start=T0.replace(hour=uur), tarief=tarief, groep=groep)


def piek_slots(tarief_nu: float = 0.10) -> tuple[PrijsSlot, ...]:
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


def invoer(**kw) -> Invoer:
    """Overcast midday, battery half full, expensive evening ahead."""
    basis = dict(
        pv_w=500.0,
        ac_load_w=1000.0,
        batterij_w=0.0,
        soc=40.0,
        boiler_temp=61.0,  # boiler klaar: warmwater blijft uit
        ev_status="disconnected",
        ev_power_w=0.0,
        tarief=0.10,
        prijs_slots=piek_slots(),
        zon_vandaag_kwh=0.0,
        zon_morgen_kwh=0.0,
    )
    basis.update(kw)
    return Invoer(**basis)


def state(**kw) -> EngineState:
    s = EngineState(**kw)
    s.legionella = LegionellaState(laatste_succes=GISTEREN)
    return s


def cmd_waarde(besluit, doel):
    for c in besluit.commandos:
        if c.doel is doel:
            return c.waarde
    return None


def test_uit_staat_alleen_droge_run():
    """arbitrage_aan=False: the plan is computed but never applied."""
    b, s = beslis(invoer(), Config(), state(), T0)
    assert b.arbitrage_plan is not None
    assert b.arbitrage_plan.actie is ArbitrageActie.VOORLADEN
    assert b.modus is Modus.ZELFVERBRUIK
    assert not b.netladen_actief and not b.piek_vasthouden_actief
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 5000.0


def test_regressie_v040_rails_zonder_nieuwe_features():
    """Default config on a plain sunny tick: v0.4.0 behavior + koel-herstel."""
    b, _ = beslis(
        invoer(pv_w=6000.0, soc=96.0, prijs_slots=(), tarief=0.20),
        Config(),
        state(),
        T0,
    )
    assert b.modus is Modus.ZELFVERBRUIK
    assert cmd_waarde(b, Doel.FEED_IN) == 5000.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 5000.0
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
    assert cmd_waarde(b, Doel.SOLAR_LIMIET_1) == 100.0
    assert cmd_waarde(b, Doel.KOEL_OFFSET) == 0.0  # only addition vs v0.4.0


def test_voorladen_rijdt_op_goedkoop_laden_rung():
    b, s = beslis(invoer(), Config(arbitrage_aan=True), state(), T0)
    assert b.modus is Modus.GOEDKOOP_LADEN
    assert b.netladen_actief
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 2000.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    assert "piek" in b.reden


def test_voorladen_respecteert_dagcap():
    s = state(netladen_uren_vandaag=3.0, netladen_datum=T0.date().isoformat())
    b, _ = beslis(invoer(), Config(arbitrage_aan=True), s, T0)
    assert not b.netladen_actief
    assert b.modus is not Modus.GOEDKOOP_LADEN


def test_vasthouden_blokkeert_ontlading():
    inv = invoer(tarief=0.25, prijs_slots=piek_slots(0.25))
    b, s = beslis(inv, Config(arbitrage_aan=True), state(), T0)
    assert b.modus is Modus.PIEK_VASTHOUDEN
    assert b.piek_vasthouden_actief
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
    assert cmd_waarde(b, Doel.FEED_IN) == 5000.0


def test_export_zet_negatief_setpoint():
    nu = T0.replace(hour=18, minute=30)
    inv = invoer(soc=90.0, tarief=0.50)
    b, s = beslis(
        inv, Config(arbitrage_aan=True, piek_export_aan=True), state(), nu
    )
    assert b.modus is Modus.PIEK_ONTLADEN
    assert b.piek_export_actief
    assert cmd_waarde(b, Doel.NET_SETPOINT) == -5000.0
    assert cmd_waarde(b, Doel.FEED_IN) == 5000.0
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 5000.0


def test_export_vereist_beide_schakelaars():
    nu = T0.replace(hour=18, minute=30)
    inv = invoer(soc=90.0, tarief=0.50)
    b, _ = beslis(inv, Config(arbitrage_aan=True), state(), nu)
    assert not b.piek_export_actief
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
    b, _ = beslis(inv, Config(piek_export_aan=True), state(), nu)
    assert not b.piek_export_actief


def test_export_onderdrukt_door_warmwater():
    nu = T0.replace(hour=18, minute=30)
    # boiler cold + big surplus: warmwater channel wants the power
    inv = invoer(soc=96.0, tarief=0.50, boiler_temp=45.0, pv_w=6000.0)
    b, _ = beslis(
        inv, Config(arbitrage_aan=True, piek_export_aan=True), state(), nu
    )
    assert b.warmwater_actief
    assert not b.piek_export_actief
    assert b.modus is Modus.WARMWATER_BOOST


def test_export_dagcap():
    nu = T0.replace(hour=18, minute=30)
    s = state(export_uren_vandaag=4.0, export_datum=nu.date().isoformat())
    inv = invoer(soc=90.0, tarief=0.50)
    b, _ = beslis(
        inv, Config(arbitrage_aan=True, piek_export_aan=True), s, nu
    )
    assert not b.piek_export_actief


def test_export_urenteller_loopt():
    nu = T0.replace(hour=18, minute=30)
    cfg = Config(arbitrage_aan=True, piek_export_aan=True)
    inv = invoer(soc=90.0, tarief=0.50)
    _, s1 = beslis(inv, cfg, state(), nu)
    assert s1.piek_export_actief
    _, s2 = beslis(inv, cfg, s1, nu + timedelta(seconds=30))
    assert s2.export_uren_vandaag > 0


def test_veilige_terugval_wist_arbitrage_vlaggen():
    s = state(piek_vasthouden_actief=True, piek_export_actief=True)
    b, s2 = beslis(invoer(pv_w=None), Config(arbitrage_aan=True), s, T0)
    assert b.modus is Modus.VEILIGE_TERUGVAL
    assert not s2.piek_vasthouden_actief and not s2.piek_export_actief


def test_noodreserve_wist_arbitrage_vlaggen():
    s = state(piek_vasthouden_actief=True, piek_export_actief=True)
    b, s2 = beslis(invoer(soc=9.0), Config(arbitrage_aan=True), s, T0)
    assert b.modus is Modus.NOODRESERVE
    assert not s2.piek_vasthouden_actief and not s2.piek_export_actief
    assert cmd_waarde(b, Doel.MAX_ONTLADING) == 0.0


def test_negatieve_prijs_wint_van_export():
    nu = T0.replace(hour=18, minute=30)
    s = state(negatieve_prijs_actief=True)
    inv = invoer(soc=90.0, tarief=-0.01)
    b, _ = beslis(
        inv, Config(arbitrage_aan=True, piek_export_aan=True), s, nu
    )
    assert not b.piek_export_actief
    assert cmd_waarde(b, Doel.FEED_IN) == 0.0
    assert cmd_waarde(b, Doel.NET_SETPOINT) == 50.0
