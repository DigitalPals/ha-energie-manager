"""Price-window logic on the Zonneplan current tariff + 8 h forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import PrijsSlot

GROEP_GOEDKOOP = "cheap"


@dataclass(frozen=True)
class Venster:
    start: datetime
    einde: datetime
    gemiddeld_tarief: float
    slots: tuple[PrijsSlot, ...]


def bouw_slots(
    nu: datetime,
    tarief_nu: float | None,
    forecast: list[tuple[float | None, str | None, datetime | None]],
) -> tuple[PrijsSlot, ...]:
    """Build the slot list: current hour + up to 8 forecast hours.

    ``forecast`` entries are (tarief, groep, start). A missing start falls back
    to positional inference (hour n starts n hours after the current hour).
    """
    slots: list[PrijsSlot] = []
    huidig_uur = nu.replace(minute=0, second=0, microsecond=0)
    if tarief_nu is not None:
        slots.append(PrijsSlot(start=huidig_uur, tarief=tarief_nu, groep=None))
    for n, (tarief, groep, start) in enumerate(forecast, start=1):
        if tarief is None:
            continue
        slots.append(
            PrijsSlot(
                start=start if start is not None else huidig_uur + timedelta(hours=n),
                tarief=tarief,
                groep=groep,
            )
        )
    return tuple(slots)


def slot_nu(slots: tuple[PrijsSlot, ...], nu: datetime) -> PrijsSlot | None:
    """The slot covering ``nu`` (slots are hourly)."""
    for slot in slots:
        if slot.start <= nu < slot.start + timedelta(hours=1):
            return slot
    return None


def slot_kwalificeert(slot: PrijsSlot, plafond: float) -> bool:
    """A slot qualifies as cheap for a function with the given price ceiling.

    Negative tariffs always qualify; otherwise the Zonneplan "cheap" banding
    must agree AND the tariff must not exceed the ceiling.
    """
    if slot.tarief < 0:
        return True
    return slot.groep == GROEP_GOEDKOOP and slot.tarief <= plafond


def nu_goedkoop(
    slots: tuple[PrijsSlot, ...], nu: datetime, tarief_nu: float | None, plafond: float
) -> bool:
    """Is the current hour a qualifying cheap hour for this ceiling?

    The current tariff (live sensor) wins over the slot list when available.
    """
    if tarief_nu is not None and tarief_nu < 0:
        return True
    slot = slot_nu(slots, nu)
    if slot is None:
        return False
    # live tariff refines the slot price when both exist
    tarief = tarief_nu if tarief_nu is not None else slot.tarief
    if tarief < 0:
        return True
    return slot.groep == GROEP_GOEDKOOP and tarief <= plafond


def goedkoopste_venster(
    slots: tuple[PrijsSlot, ...], duur_uren: int, niet_later_dan: datetime | None = None
) -> Venster | None:
    """Cheapest contiguous window of ``duur_uren`` whole hours.

    Only considers windows of consecutive hourly slots; ``niet_later_dan``
    bounds the window END (deadline semantics).
    """
    if duur_uren < 1 or len(slots) < duur_uren:
        return None
    geordend = sorted(slots, key=lambda s: s.start)
    beste: Venster | None = None
    for i in range(len(geordend) - duur_uren + 1):
        kandidaat = geordend[i : i + duur_uren]
        # must be consecutive hours
        aaneengesloten = all(
            kandidaat[j + 1].start - kandidaat[j].start == timedelta(hours=1)
            for j in range(len(kandidaat) - 1)
        )
        if not aaneengesloten:
            continue
        einde = kandidaat[-1].start + timedelta(hours=1)
        if niet_later_dan is not None and einde > niet_later_dan:
            continue
        gemiddeld = sum(s.tarief for s in kandidaat) / duur_uren
        if beste is None or gemiddeld < beste.gemiddeld_tarief:
            beste = Venster(
                start=kandidaat[0].start,
                einde=einde,
                gemiddeld_tarief=gemiddeld,
                slots=tuple(kandidaat),
            )
    return beste
