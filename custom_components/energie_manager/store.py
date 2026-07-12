"""Persistence of the engine state via the HA Store helper."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .core.model import EngineState, LegionellaState, Modus, SessieRecord, SessieState

OPSLAG_VERSIE = 1
DEBOUNCE_S = 10.0


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _dt(waarde: str | None) -> datetime | None:
    return datetime.fromisoformat(waarde) if waarde else None


def state_naar_dict(s: EngineState) -> dict[str, Any]:
    """Persisted subset; hysteresis sub-timers deliberately excluded."""
    return {
        "actieve_modus": str(s.actieve_modus),
        "modus_sinds": _iso(s.modus_sinds),
        "dwell_tot": _iso(s.dwell_tot),
        "warmwater_actief": s.warmwater_actief,
        "ev_actief": s.ev_actief,
        "ev_ampere": s.ev_ampere,
        "ev_direct_laden": s.ev_direct_laden,
        "netladen_actief": s.netladen_actief,
        "negatieve_prijs_actief": s.negatieve_prijs_actief,
        "legionella": {
            "laatste_succes": _iso(s.legionella.laatste_succes),
            "hold_start": _iso(s.legionella.hold_start),
            "hold_pauze_sinds": _iso(s.legionella.hold_pauze_sinds),
            "forceer_actief": s.legionella.forceer_actief,
            "gepland_voor": _iso(s.legionella.gepland_voor),
        },
        "netladen_uren_vandaag": s.netladen_uren_vandaag,
        "netladen_datum": s.netladen_datum,
        "geforceerde_modus": str(s.geforceerde_modus) if s.geforceerde_modus else None,
        "geforceerd_tot": _iso(s.geforceerd_tot),
        "sessie": {
            "actief": s.sessie.actief,
            "start": _iso(s.sessie.start),
            "laatste_meter_kwh": s.sessie.laatste_meter_kwh,
            "energie_kwh": s.sessie.energie_kwh,
            "energie_gratis_kwh": s.sessie.energie_gratis_kwh,
            "energie_net_kwh": s.sessie.energie_net_kwh,
            "energie_ongeprijsd_kwh": s.sessie.energie_ongeprijsd_kwh,
            "kosten_eur": s.sessie.kosten_eur,
        },
        "sessie_historie": [
            {
                "start": _iso(r.start),
                "einde": _iso(r.einde),
                "energie_kwh": r.energie_kwh,
                "energie_gratis_kwh": r.energie_gratis_kwh,
                "energie_net_kwh": r.energie_net_kwh,
                "energie_ongeprijsd_kwh": r.energie_ongeprijsd_kwh,
                "kosten_eur": r.kosten_eur,
            }
            for r in s.sessie_historie
        ],
    }


def state_uit_dict(data: dict[str, Any] | None) -> EngineState:
    s = EngineState()
    if not data:
        return s
    try:
        s.actieve_modus = Modus(data.get("actieve_modus", "zelfverbruik"))
    except ValueError:
        s.actieve_modus = Modus.ZELFVERBRUIK
    s.modus_sinds = _dt(data.get("modus_sinds"))
    s.dwell_tot = _dt(data.get("dwell_tot"))
    s.warmwater_actief = bool(data.get("warmwater_actief", False))
    s.ev_actief = bool(data.get("ev_actief", False))
    s.ev_ampere = int(data.get("ev_ampere", 0))
    s.ev_direct_laden = bool(data.get("ev_direct_laden", False))
    s.netladen_actief = bool(data.get("netladen_actief", False))
    s.negatieve_prijs_actief = bool(data.get("negatieve_prijs_actief", False))
    leg = data.get("legionella") or {}
    s.legionella = LegionellaState(
        laatste_succes=_dt(leg.get("laatste_succes")),
        hold_start=_dt(leg.get("hold_start")),
        hold_pauze_sinds=_dt(leg.get("hold_pauze_sinds")),
        forceer_actief=bool(leg.get("forceer_actief", False)),
        gepland_voor=_dt(leg.get("gepland_voor")),
    )
    s.netladen_uren_vandaag = float(data.get("netladen_uren_vandaag", 0.0))
    s.netladen_datum = data.get("netladen_datum")
    forced = data.get("geforceerde_modus")
    if forced:
        try:
            s.geforceerde_modus = Modus(forced)
        except ValueError:
            s.geforceerde_modus = None
    s.geforceerd_tot = _dt(data.get("geforceerd_tot"))
    ses = data.get("sessie") or {}
    meter = ses.get("laatste_meter_kwh")
    s.sessie = SessieState(
        actief=bool(ses.get("actief", False)),
        start=_dt(ses.get("start")),
        laatste_meter_kwh=float(meter) if meter is not None else None,
        energie_kwh=float(ses.get("energie_kwh", 0.0)),
        energie_gratis_kwh=float(ses.get("energie_gratis_kwh", 0.0)),
        energie_net_kwh=float(ses.get("energie_net_kwh", 0.0)),
        energie_ongeprijsd_kwh=float(ses.get("energie_ongeprijsd_kwh", 0.0)),
        kosten_eur=float(ses.get("kosten_eur", 0.0)),
    )
    s.sessie_historie = []
    for rec in data.get("sessie_historie") or []:
        start = _dt(rec.get("start"))
        einde = _dt(rec.get("einde"))
        if start is None or einde is None:
            continue  # malformed record: skip defensively
        s.sessie_historie.append(
            SessieRecord(
                start=start,
                einde=einde,
                energie_kwh=float(rec.get("energie_kwh", 0.0)),
                energie_gratis_kwh=float(rec.get("energie_gratis_kwh", 0.0)),
                energie_net_kwh=float(rec.get("energie_net_kwh", 0.0)),
                energie_ongeprijsd_kwh=float(rec.get("energie_ongeprijsd_kwh", 0.0)),
                kosten_eur=float(rec.get("kosten_eur", 0.0)),
            )
        )
    return s


class EnergieManagerStore:
    """Debounced persistence wrapper around the HA Store helper."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, OPSLAG_VERSIE, f"{DOMAIN}.{entry_id}"
        )

    async def laad(self) -> EngineState:
        return state_uit_dict(await self._store.async_load())

    def bewaar_vertraagd(self, state: EngineState) -> None:
        data = state_naar_dict(state)
        self._store.async_delay_save(lambda: data, DEBOUNCE_S)

    async def bewaar_direct(self, state: EngineState) -> None:
        await self._store.async_save(state_naar_dict(state))
