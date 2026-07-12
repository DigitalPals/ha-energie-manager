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
