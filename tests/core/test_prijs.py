from datetime import datetime, timedelta

from custom_components.energie_manager.core.model import PrijsSlot
from custom_components.energie_manager.core.prijs import (
    bouw_slots,
    goedkoopste_venster,
    nu_goedkoop,
    slot_kwalificeert,
    slot_nu,
)

T0 = datetime(2026, 7, 12, 13, 20)
UUR = T0.replace(minute=0)


def test_bouw_slots_positioneel():
    slots = bouw_slots(T0, 0.25, [(0.20, "normal", None), (0.10, "cheap", None)])
    assert len(slots) == 3
    assert slots[0].start == UUR and slots[0].tarief == 0.25
    assert slots[1].start == UUR + timedelta(hours=1)
    assert slots[2].start == UUR + timedelta(hours=2) and slots[2].groep == "cheap"


def test_bouw_slots_expliciete_start_en_gaten():
    start = UUR + timedelta(hours=3)
    slots = bouw_slots(T0, None, [(None, None, None), (0.05, "cheap", start)])
    assert len(slots) == 1
    assert slots[0].start == start


def test_slot_kwalificeert():
    assert slot_kwalificeert(PrijsSlot(UUR, -0.01, "expensive"), plafond=0.0)
    assert slot_kwalificeert(PrijsSlot(UUR, 0.0, "cheap"), plafond=0.0)
    assert not slot_kwalificeert(PrijsSlot(UUR, 0.01, "cheap"), plafond=0.0)
    assert slot_kwalificeert(PrijsSlot(UUR, 0.05, "cheap"), plafond=0.10)
    assert not slot_kwalificeert(PrijsSlot(UUR, 0.05, "normal"), plafond=0.10)


def test_nu_goedkoop_live_tarief_wint():
    slots = (PrijsSlot(UUR, 0.20, "normal"),)
    assert nu_goedkoop(slots, T0, -0.05, plafond=0.0)  # live negative wins
    assert not nu_goedkoop(slots, T0, 0.20, plafond=0.0)
    # cheap-banded slot, live tariff below ceiling
    slots = (PrijsSlot(UUR, 0.08, "cheap"),)
    assert nu_goedkoop(slots, T0, 0.09, plafond=0.10)
    assert not nu_goedkoop(slots, T0, 0.11, plafond=0.10)
    # no slot covering now
    assert not nu_goedkoop((), T0, 0.05, plafond=0.10)


def test_slot_nu():
    slots = (PrijsSlot(UUR, 0.2, None), PrijsSlot(UUR + timedelta(hours=1), 0.1, None))
    assert slot_nu(slots, T0).tarief == 0.2
    assert slot_nu(slots, UUR + timedelta(hours=1, minutes=59)).tarief == 0.1
    assert slot_nu(slots, UUR + timedelta(hours=2)) is None


def test_goedkoopste_venster():
    slots = tuple(
        PrijsSlot(UUR + timedelta(hours=n), tarief, None)
        for n, tarief in enumerate([0.30, 0.20, 0.05, 0.10, 0.25])
    )
    v = goedkoopste_venster(slots, 2)
    assert v.start == UUR + timedelta(hours=2)
    assert v.gemiddeld_tarief == (0.05 + 0.10) / 2
    # deadline bounds the window end
    v = goedkoopste_venster(slots, 2, niet_later_dan=UUR + timedelta(hours=3))
    assert v.start == UUR + timedelta(hours=1)


def test_goedkoopste_venster_niet_aaneengesloten():
    slots = (
        PrijsSlot(UUR, 0.1, None),
        PrijsSlot(UUR + timedelta(hours=2), 0.1, None),  # gap at hour 1
    )
    assert goedkoopste_venster(slots, 2) is None
    assert goedkoopste_venster(slots, 1).start == UUR


# --------------------------------------------------------------------- #
# v0.5.0: expensive-hour helpers + Zonneplan attribute slots             #
# --------------------------------------------------------------------- #

from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from custom_components.energie_manager.core.prijs import (  # noqa: E402
    bouw_slots_zonneplan,
    goedkoopste_slots,
    is_piek,
    piek_vensters,
)


def _slot(uur, tarief, groep=None, dag=0):
    return PrijsSlot(
        start=UUR.replace(hour=uur) + timedelta(days=dag), tarief=tarief, groep=groep
    )


def test_is_piek_op_groep_en_drempel():
    assert is_piek(_slot(18, 0.20, "expensive"), drempel=0.35)
    assert is_piek(_slot(18, 0.40), drempel=0.35)
    assert not is_piek(_slot(18, 0.30, "normal"), drempel=0.35)


def test_piek_vensters_aaneengesloten_runs():
    slots = (
        _slot(14, 0.40),
        _slot(15, 0.40),
        _slot(17, 0.50, "expensive"),  # gap at 16:00 splits the runs
        _slot(18, 0.10),
    )
    vensters = piek_vensters(slots, UUR, drempel=0.35)
    assert len(vensters) == 2
    assert vensters[0].start.hour == 14 and vensters[0].einde.hour == 16
    assert vensters[1].start.hour == 17
    assert vensters[0].gemiddeld_tarief == pytest.approx(0.40)


def test_piek_vensters_verleden_valt_af():
    slots = (_slot(8, 0.50, "expensive"), _slot(9, 0.50, "expensive"))
    assert piek_vensters(slots, UUR, drempel=0.35) == ()


def test_piek_venster_half_verstreken_telt_mee():
    slots = (_slot(11, 0.50, "expensive"), _slot(12, 0.50, "expensive"))
    vensters = piek_vensters(slots, UUR.replace(hour=12, minute=30), drempel=0.35)
    assert len(vensters) == 1
    assert vensters[0].start.hour == 11


def test_goedkoopste_slots_deadline_en_sortering():
    slots = (
        _slot(12, 0.20),
        _slot(13, 0.05),
        _slot(14, 0.10),
        _slot(15, 0.01),  # after the deadline
    )
    gekozen = goedkoopste_slots(slots, 2, niet_later_dan=UUR.replace(hour=15))
    assert [s.start.hour for s in gekozen] == [13, 14]  # by start, not price


def test_bouw_slots_zonneplan_merge_en_verleden():
    forecast = [
        (0.10, "cheap", UUR.replace(hour=10), None),  # fully past: dropped
        (0.25, "normal", UUR.replace(hour=12), 40.0),  # covers nu
        (0.50, "expensive", UUR.replace(hour=13), 0.0),
    ]
    slots = bouw_slots_zonneplan(UUR.replace(hour=12, minute=30), 0.30, forecast)
    assert len(slots) == 2
    assert slots[0].tarief == 0.30  # live tariff refines the current slot
    assert slots[0].groep == "normal" and slots[0].zon_pct == 40.0
    assert slots[1].groep == "expensive"


def test_bouw_slots_zonneplan_zonder_dekking_van_nu():
    forecast = [(0.50, "expensive", UUR.replace(hour=15), None)]
    slots = bouw_slots_zonneplan(UUR, 0.20, forecast)
    assert slots[0].start == UUR and slots[0].tarief == 0.20
    assert slots[1].start.hour == 15


def test_bouw_slots_zonneplan_dst_veilig():
    # DST end (2026-10-25, Amsterdam): aware datetimes keep hourly contiguity
    tz = ZoneInfo("Europe/Amsterdam")
    basis = datetime(2026, 10, 25, 1, 0, tzinfo=tz)
    forecast = [
        (0.10, None, basis + timedelta(hours=n), None) for n in range(4)
    ]
    slots = bouw_slots_zonneplan(basis, 0.10, forecast)
    assert len(slots) == 4
    vensters = piek_vensters(
        tuple(PrijsSlot(s.start, 0.50, "expensive") for s in slots), basis, 0.35
    )
    assert len(vensters) == 1  # one contiguous run across the DST fold
