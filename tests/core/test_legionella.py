from datetime import datetime, timedelta

from custom_components.energie_manager.core.legionella import (
    plan,
    start_nu,
    update_hold,
)
from custom_components.energie_manager.core.model import (
    Config,
    LegionellaState,
    PrijsSlot,
)

CONFIG = Config()
T0 = datetime(2026, 7, 12, 15, 0)  # inside the 14:00-20:00 window


def _hold_reeks(state, temps, start=T0, stap_min=1.0):
    """Feed a temperature series, one sample per stap_min minutes."""
    resultaat = None
    for i, temp in enumerate(temps):
        resultaat = update_hold(state, temp, start + timedelta(minutes=i * stap_min), CONFIG)
    return resultaat


def test_hold_succes_na_20_minuten():
    state = LegionellaState()
    r = _hold_reeks(state, [61.0] * 21)
    assert r.succes
    assert state.laatste_succes == T0 + timedelta(minutes=20)
    assert state.hold_start is None


def test_hold_drempel_is_doel_minus_01():
    state = LegionellaState()
    r = _hold_reeks(state, [60.9] * 21)  # >= 60.9 counts ("above 60.9" live semantics)
    assert r.succes
    state = LegionellaState()
    r = _hold_reeks(state, [60.8] * 21)
    assert not r.succes and not r.bezig


def test_hold_korte_dip_pauzeert():
    state = LegionellaState()
    # 10 min hot, 14 min dip, then hot again: hold resumes, paused time excluded
    temps = [61.5] * 10 + [59.0] * 14 + [61.5] * 11
    r = _hold_reeks(state, temps)
    assert r.succes  # 10 + 11 minutes >= 20 (dip did not reset)


def test_hold_lange_dip_reset():
    state = LegionellaState()
    temps = [61.5] * 10 + [59.0] * 16 + [61.5] * 15
    r = _hold_reeks(state, temps)
    assert not r.succes
    assert r.bezig  # new streak building


def test_hold_succes_wist_forceer():
    state = LegionellaState(forceer_actief=True)
    r = _hold_reeks(state, [61.2] * 21)
    assert r.succes
    assert not state.forceer_actief


def test_plan_dag6_start_in_venster():
    state = LegionellaState(laatste_succes=T0 - timedelta(days=6, hours=1))
    r = plan(state, T0, (), None, None, CONFIG)
    assert r.forceer
    assert state.forceer_actief


def test_plan_dag6_uitstel_bij_zonniger_morgen():
    # success 7 days ago at 18:00; now 18:30 on day 6 -> deadline tomorrow
    # 18:00 leaves room for tomorrow's window (14:05 + 2h buffer)
    nu = T0.replace(hour=18, minute=30)
    state = LegionellaState(laatste_succes=nu.replace(hour=18, minute=0) - timedelta(days=6))
    r = plan(state, nu, (), 4.0, 10.0, CONFIG)
    assert not r.forceer
    assert r.gepland_voor.date() == (nu + timedelta(days=1)).date()
    assert r.gepland_voor.hour == 14


def test_plan_dag6_geen_uitstel_als_deadline_te_krap():
    # success 6 days 1 h ago: deadline lands before tomorrow's window,
    # so it must run today despite a sunnier tomorrow
    state = LegionellaState(laatste_succes=T0 - timedelta(days=6, hours=1))
    r = plan(state, T0.replace(hour=15), (), 4.0, 10.0, CONFIG)
    assert r.forceer


def test_plan_deadline_verstreken_forceert():
    state = LegionellaState(laatste_succes=T0 - timedelta(days=8))
    r = plan(state, T0, (), 20.0, 20.0, CONFIG)  # sunny tomorrow is irrelevant now
    assert r.forceer


def test_plan_deadline_verstreken_buiten_venster_wacht():
    nu = T0.replace(hour=9)
    state = LegionellaState(laatste_succes=nu - timedelta(days=8))
    r = plan(state, nu, (), None, None, CONFIG)
    assert not r.forceer
    assert r.gepland_voor == nu.replace(hour=14, minute=5)


def test_plan_avondstop():
    state = LegionellaState(
        laatste_succes=T0 - timedelta(days=8), forceer_actief=True
    )
    r = plan(state, T0.replace(hour=20), (), None, None, CONFIG)
    assert not r.forceer
    assert not state.forceer_actief


def test_plan_geen_actie_voor_dag6():
    state = LegionellaState(laatste_succes=T0 - timedelta(days=3))
    r = plan(state, T0, (), None, None, CONFIG)
    assert not r.forceer
    assert r.gepland_voor is None


def test_plan_nooit_succes_geldt_als_verlopen():
    state = LegionellaState(laatste_succes=None)
    r = plan(state, T0, (), None, None, CONFIG)
    assert r.forceer


def test_plan_uitstel_bij_goedkopere_morgen():
    nu = T0.replace(hour=18, minute=30)
    state = LegionellaState(laatste_succes=nu.replace(hour=18, minute=0) - timedelta(days=6))
    morgen = nu + timedelta(days=1)
    slots = (
        PrijsSlot(nu.replace(hour=19, minute=0), 0.30, None),
        PrijsSlot(morgen.replace(hour=14, minute=0), 0.10, "cheap"),
    )
    r = plan(state, nu, slots, None, None, CONFIG)
    assert not r.forceer
    assert r.gepland_voor.date() == morgen.date()


def test_start_nu():
    state = LegionellaState()
    start_nu(state)
    assert state.forceer_actief
