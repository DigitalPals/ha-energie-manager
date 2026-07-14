"""Price-window logic on the Zonneplan current tariff + 8 h forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import PrijsSlot

GROEP_GOEDKOOP = "cheap"
GROEP_DUUR = "expensive"


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


def bouw_slots_zonneplan(
    nu: datetime,
    tarief_nu: float | None,
    forecast: list[tuple[float | None, str | None, datetime | None, float | None]],
) -> tuple[PrijsSlot, ...]:
    """Build the slot list from the Zonneplan forecast attribute array.

    ``forecast`` entries are (tarief, groep, start, zon_pct); entries without
    a tariff or start are skipped, fully-past slots dropped. The live tariff
    refines the slot covering ``nu``; if no slot covers ``nu`` a bare
    current-hour slot is prepended so ``slot_nu`` keeps working.
    """
    huidig_uur = nu.replace(minute=0, second=0, microsecond=0)
    slots: list[PrijsSlot] = []
    for tarief, groep, start, zon_pct in forecast:
        if tarief is None or start is None:
            continue
        if start + timedelta(hours=1) <= nu:
            continue  # fully in the past
        slots.append(PrijsSlot(start=start, tarief=tarief, groep=groep, zon_pct=zon_pct))
    slots.sort(key=lambda s: s.start)
    dekt_nu = any(s.start <= nu < s.start + timedelta(hours=1) for s in slots)
    if tarief_nu is not None:
        if dekt_nu:
            slots = [
                PrijsSlot(s.start, tarief_nu, s.groep, s.zon_pct)
                if s.start <= nu < s.start + timedelta(hours=1)
                else s
                for s in slots
            ]
        else:
            slots.insert(0, PrijsSlot(start=huidig_uur, tarief=tarief_nu, groep=None))
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


def is_piek(slot: PrijsSlot, drempel: float) -> bool:
    """Is this slot an expensive/peak hour?

    Zonneplan's own banding is relative (catches peaks on cheap days); the
    absolute threshold catches missing or degenerate banding. Whether acting
    on a peak is *worth it* is a separate economics gate (core.arbitrage).
    """
    return slot.groep == GROEP_DUUR or slot.tarief >= drempel


def piek_vensters(
    slots: tuple[PrijsSlot, ...], nu: datetime, drempel: float
) -> tuple[Venster, ...]:
    """Contiguous runs of peak slots that have not fully passed, by start."""
    geordend = sorted((s for s in slots if is_piek(s, drempel)), key=lambda s: s.start)
    vensters: list[Venster] = []
    run: list[PrijsSlot] = []

    def _sluit_af() -> None:
        if not run:
            return
        einde = run[-1].start + timedelta(hours=1)
        if einde > nu:
            vensters.append(
                Venster(
                    start=run[0].start,
                    einde=einde,
                    gemiddeld_tarief=sum(s.tarief for s in run) / len(run),
                    slots=tuple(run),
                )
            )

    for slot in geordend:
        if run and slot.start - run[-1].start != timedelta(hours=1):
            _sluit_af()
            run = []
        run.append(slot)
    _sluit_af()
    return tuple(vensters)


def goedkoopste_slots(
    slots: tuple[PrijsSlot, ...], aantal: int, niet_later_dan: datetime | None = None
) -> tuple[PrijsSlot, ...]:
    """The ``aantal`` cheapest (not necessarily contiguous) whole-hour slots.

    ``niet_later_dan`` bounds each slot's END (deadline semantics, like
    ``goedkoopste_venster``). Result is sorted by start time.
    """
    if aantal < 1:
        return ()
    kandidaten = [
        s
        for s in slots
        if niet_later_dan is None or s.start + timedelta(hours=1) <= niet_later_dan
    ]
    kandidaten.sort(key=lambda s: (s.tarief, s.start))
    gekozen = kandidaten[:aantal]
    gekozen.sort(key=lambda s: s.start)
    return tuple(gekozen)
