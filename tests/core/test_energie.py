"""Tests for the battery SoC prognosis model."""

from datetime import datetime, timedelta

import pytest

from custom_components.energie_manager.core.energie import bruikbaar_kwh, prognose
from custom_components.energie_manager.core.model import PrijsSlot

T0 = datetime(2026, 7, 12, 12, 0)


def slot(uur: int, tarief: float = 0.20, zon_pct: float | None = None) -> PrijsSlot:
    return PrijsSlot(start=T0.replace(hour=uur), tarief=tarief, zon_pct=zon_pct)


def _prognose(**kw):
    basis = dict(
        soc=50.0,
        capaciteit_kwh=60.0,
        zon_restant_kwh=0.0,
        slots=(),
        huislast_kw=1.0,
        nu=T0,
        doel_soc=95.0,
        reserve_soc=25.0,
        zon_einde_uur=21,
    )
    basis.update(kw)
    return prognose(**basis)


def test_bruikbaar_kwh():
    assert bruikbaar_kwh(65.0, 35.0, 60.0) == pytest.approx(18.0)
    assert bruikbaar_kwh(30.0, 35.0, 60.0) == 0.0


def test_zonder_zon_daalt_soc_met_huislast():
    prog = _prognose()
    # 6 h x 1 kW = 6 kWh = 10% of 60 kWh
    assert prog.soc_op(T0 + timedelta(hours=6)) == pytest.approx(40.0)
    assert prog.vol_om is None


def test_uniforme_zonverdeling_houdt_soc_vlak():
    # 9 daylight hours left (12..20), 9 kWh remaining -> 1 kWh/h = huislast
    prog = _prognose(zon_restant_kwh=9.0)
    assert prog.soc_op(T0 + timedelta(hours=6)) == pytest.approx(50.0)
    # after zon_einde_uur the house load drains the battery again
    assert prog.soc_op(T0 + timedelta(hours=11)) < 50.0


def test_zon_pct_gewichten_winnen_van_uniform():
    # all solar lands in the 13:00 slot
    slots = (slot(12, zon_pct=0), slot(13, zon_pct=100), slot(14, zon_pct=0))
    prog = _prognose(zon_restant_kwh=12.0, slots=slots)
    # 12:00-13:00: only house load -> 50 - 1/60*100 ≈ 48.3
    assert prog.soc_op(T0 + timedelta(hours=1)) == pytest.approx(48.33, abs=0.1)
    # 13:00-14:00: +12 kWh -1 kWh = +11 kWh -> +18.3%
    assert prog.soc_op(T0 + timedelta(hours=2)) == pytest.approx(66.67, abs=0.1)


def test_vol_om_en_klem_op_100():
    prog = _prognose(zon_restant_kwh=90.0, soc=80.0)
    assert prog.vol_om is not None
    assert prog.vol_om <= T0 + timedelta(hours=3)
    assert max(soc for _, soc in prog.pad) == 100.0


def test_klem_op_reserve():
    prog = _prognose(soc=27.0)
    assert min(soc for _, soc in prog.pad) == 25.0


def test_eerste_uur_pro_rata():
    nu = T0 + timedelta(minutes=30)
    prog = _prognose(nu=nu)
    # half an hour of 1 kW = 0.5 kWh ≈ 0.83%
    assert prog.soc_op(T0 + timedelta(hours=1)) == pytest.approx(49.17, abs=0.05)


def test_soc_al_op_doel():
    prog = _prognose(soc=96.0)
    assert prog.vol_om == T0


def test_capaciteit_nul_degenereert_veilig():
    prog = _prognose(capaciteit_kwh=0.0)
    assert prog.soc_op(T0 + timedelta(hours=3)) == 50.0
    assert prog.vol_om is None


def test_zon_none_is_nul():
    prog = _prognose(zon_restant_kwh=None)
    assert prog.soc_op(T0 + timedelta(hours=6)) == pytest.approx(40.0)
