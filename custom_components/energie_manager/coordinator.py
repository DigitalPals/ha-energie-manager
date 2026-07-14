"""The 30-second decision loop."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AC_VERBRUIK,
    CONF_BATTERIJ_SOC,
    CONF_BATTERIJ_VERMOGEN,
    CONF_BOILER_TEMPERATUUR,
    CONF_EV_SESSIE_ENERGIE,
    CONF_EV_STATUS_RAW,
    CONF_EV_VERMOGEN,
    CONF_FORECAST_GROEP_PATROON,
    CONF_FORECAST_TARIEF_PATROON,
    CONF_NET_VERMOGEN,
    CONF_OVERSCHOT_EXTERN,
    CONF_PV_VERMOGEN,
    CONF_TARIEF,
    CONF_ZON_MORGEN,
    CONF_ZON_VANDAAG,
    DOMAIN,
    EVENT_BESLUIT,
    FORECAST_UREN,
    MAX_LEEFTIJD_S,
    OPT_AUTOMATISCH_BEHEER,
    UPDATE_INTERVAL_S,
    config_uit_options,
)
from .core import legionella as leg_mod
from .core.engine import beslis
from .core.ev import decodeer_status
from .core.model import Besluit, Doel, Invoer, Modus
from .core.prijs import bouw_slots
from .executor import Uitvoerder
from .store import EnergieManagerStore

_LOGGER = logging.getLogger(__name__)

GESCHIEDENIS_MAX = 100


class EnergieManagerCoordinator(DataUpdateCoordinator[Besluit]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        store: EnergieManagerStore,
        uitvoerder: Uitvoerder,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_S),
        )
        self.entry = entry
        self.store = store
        self.uitvoerder = uitvoerder
        self.mapping_snapshot = dict(entry.data)
        self.engine_state = None  # set in async_initialiseer
        self.geschiedenis: deque[dict[str, Any]] = deque(maxlen=GESCHIEDENIS_MAX)
        self.laatste_invoer: Invoer | None = None
        self.verouderd: list[str] = []
        self.lang_ongewijzigd: list[str] = []

    async def async_initialiseer(self) -> None:
        self.engine_state = await self.store.laad()

    # ------------------------------------------------------------- #
    # input gathering                                                 #
    # ------------------------------------------------------------- #

    def _mapping(self, sleutel: str) -> str | None:
        waarde = self.entry.data.get(sleutel)
        return waarde or None

    def _lees_float(self, sleutel: str) -> float | None:
        entity_id = self._mapping(sleutel)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            self.verouderd.append(sleutel)
            return None
        # Age is informational only: template-proxied sources (victron_gx
        # compat sensors) only re-report on value change, so a constant
        # value is indistinguishable from a frozen source. Real outages
        # arrive as unavailable/unknown via their availability templates.
        max_leeftijd = MAX_LEEFTIJD_S.get(sleutel)
        if max_leeftijd is not None:
            leeftijd = (dt_util.utcnow() - state.last_reported).total_seconds()
            if leeftijd > max_leeftijd:
                self.lang_ongewijzigd.append(f"{sleutel} ({leeftijd:.0f}s)")
        try:
            return float(state.state)
        except ValueError:
            self.verouderd.append(sleutel)
            return None

    def _lees_bool_switch(self, entity_id: str | None) -> bool | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state == "on"

    def _lees_forecast(self) -> list[tuple[float | None, str | None, datetime | None]]:
        tarief_patroon = self.entry.data.get(CONF_FORECAST_TARIEF_PATROON) or ""
        groep_patroon = self.entry.data.get(CONF_FORECAST_GROEP_PATROON) or ""
        forecast: list[tuple[float | None, str | None, datetime | None]] = []
        if "{n}" not in tarief_patroon:
            return forecast
        for n in range(1, FORECAST_UREN + 1):
            tarief: float | None = None
            groep: str | None = None
            start: datetime | None = None
            t_state = self.hass.states.get(tarief_patroon.format(n=n))
            if t_state is not None and t_state.state not in ("unknown", "unavailable"):
                try:
                    tarief = float(t_state.state)
                except ValueError:
                    tarief = None
                # prefer an explicit timestamp attribute when the sensor has one
                for attr in ("datetime", "timestamp", "start"):
                    ruw = t_state.attributes.get(attr)
                    if isinstance(ruw, str):
                        start = dt_util.parse_datetime(ruw)
                    elif isinstance(ruw, datetime):
                        start = ruw
                    if start is not None:
                        start = dt_util.as_local(start)
                        break
            if "{n}" in groep_patroon:
                g_state = self.hass.states.get(groep_patroon.format(n=n))
                if g_state is not None and g_state.state not in ("unknown", "unavailable"):
                    groep = g_state.state.lower()
            forecast.append((tarief, groep, start))
        return forecast

    def _som_entiteiten(self, sleutel: str) -> float | None:
        entity_ids = self.entry.data.get(sleutel) or []
        if not entity_ids:
            return None
        totaal = 0.0
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                return None
            try:
                totaal += float(state.state)
            except ValueError:
                return None
        return totaal

    def bouw_invoer(self, nu: datetime) -> Invoer:
        self.verouderd = []
        self.lang_ongewijzigd = []
        ev_raw = self._lees_float(CONF_EV_STATUS_RAW)
        extern = None
        if self._mapping(CONF_OVERSCHOT_EXTERN):
            extern = self._lees_float(CONF_OVERSCHOT_EXTERN)
        return Invoer(
            pv_w=self._lees_float(CONF_PV_VERMOGEN),
            ac_load_w=self._lees_float(CONF_AC_VERBRUIK),
            batterij_w=self._lees_float(CONF_BATTERIJ_VERMOGEN),
            overschot_extern_kw=extern,
            soc=self._lees_float(CONF_BATTERIJ_SOC),
            boiler_temp=self._lees_float(CONF_BOILER_TEMPERATUUR),
            ev_status=decodeer_status(int(ev_raw) if ev_raw is not None else None),
            ev_power_w=self._lees_float(CONF_EV_VERMOGEN),
            ev_sessie_energie_kwh=self._lees_float(CONF_EV_SESSIE_ENERGIE),
            net_vermogen_w=self._lees_float(CONF_NET_VERMOGEN),
            tarief=self._lees_float(CONF_TARIEF),
            prijs_slots=bouw_slots(nu, self._lees_float(CONF_TARIEF), self._lees_forecast()),
            zon_vandaag_kwh=self._som_entiteiten(CONF_ZON_VANDAAG),
            zon_morgen_kwh=self._som_entiteiten(CONF_ZON_MORGEN),
            relais_aan=self._lees_bool_switch(
                self.uitvoerder.entity_id(Doel.WARMWATER_RELAIS)
            ),
            ev_laden_aan=self._lees_bool_switch(
                self.uitvoerder.entity_id(Doel.EV_SCHAKELAAR)
            ),
            verouderd=tuple(self.verouderd),
        )

    # ------------------------------------------------------------- #
    # the tick                                                        #
    # ------------------------------------------------------------- #

    async def _async_update_data(self) -> Besluit:
        nu = dt_util.now()
        invoer = self.bouw_invoer(nu)
        self.laatste_invoer = invoer
        config = config_uit_options(dict(self.entry.options))

        vorige = self.engine_state
        vorige_succes = vorige.legionella.laatste_succes if vorige else None
        vorige_neg = vorige.negatieve_prijs_actief if vorige else False
        vorige_sessie_kop = (
            vorige.sessie_historie[0] if vorige and vorige.sessie_historie else None
        )

        besluit, nieuwe_state = beslis(invoer, config, self.engine_state, nu)
        self.engine_state = nieuwe_state

        if self.automatisch_beheer:
            await self.uitvoerder.voer_uit(besluit.commandos)

        nieuwe_sessie_kop = (
            nieuwe_state.sessie_historie[0] if nieuwe_state.sessie_historie else None
        )
        # persistence: immediate for health/price-critical transitions
        if (
            nieuwe_state.legionella.laatste_succes != vorige_succes
            or nieuwe_state.negatieve_prijs_actief != vorige_neg
            or nieuwe_sessie_kop != vorige_sessie_kop
        ):
            await self.store.bewaar_direct(nieuwe_state)
        else:
            self.store.bewaar_vertraagd(nieuwe_state)

        self._meld_transitie(besluit, nu)
        return besluit

    @property
    def automatisch_beheer(self) -> bool:
        return bool(self.entry.options.get(OPT_AUTOMATISCH_BEHEER, False))

    def _meld_transitie(self, besluit: Besluit, nu: datetime) -> None:
        vorige = self.data
        if (
            vorige is not None
            and vorige.modus == besluit.modus
            and vorige.overlays == besluit.overlays
            and vorige.warmwater_actief == besluit.warmwater_actief
            and vorige.ev_actief == besluit.ev_actief
            and vorige.netladen_actief == besluit.netladen_actief
        ):
            return
        gegevens = {
            "oude_modus": str(vorige.modus) if vorige else None,
            "nieuwe_modus": str(besluit.modus),
            "overlays": [str(o) for o in besluit.overlays],
            "reden": besluit.reden,
            "uitgevoerd": self.automatisch_beheer,
        }
        self.geschiedenis.appendleft({"tijd": nu.isoformat(), **gegevens})
        self.hass.bus.async_fire(EVENT_BESLUIT, gegevens)
        _LOGGER.info(
            "%s -> %s (%s)%s",
            gegevens["oude_modus"],
            gegevens["nieuwe_modus"],
            besluit.reden,
            "" if self.automatisch_beheer else " [niet uitgevoerd: beheer uit]",
        )

    # ------------------------------------------------------------- #
    # service / entity hooks                                          #
    # ------------------------------------------------------------- #

    async def forceer_modus(self, modus: Modus | None, duur: timedelta) -> None:
        if self.engine_state is None:
            return
        if modus is None:
            self.engine_state.geforceerde_modus = None
            self.engine_state.geforceerd_tot = None
        else:
            self.engine_state.geforceerde_modus = modus
            self.engine_state.geforceerd_tot = dt_util.now() + duur
        await self.async_request_refresh()

    async def zet_ev_direct_laden(self, aan: bool) -> None:
        if self.engine_state is None:
            return
        self.engine_state.ev_direct_laden = aan
        await self.store.bewaar_direct(self.engine_state)
        await self.async_request_refresh()

    async def start_legionella(self) -> None:
        if self.engine_state is None:
            return
        leg_mod.start_nu(self.engine_state.legionella)
        await self.store.bewaar_direct(self.engine_state)
        await self.async_request_refresh()

    async def zet_legionella_succes(self, tijdstip: datetime) -> None:
        if self.engine_state is None:
            return
        self.engine_state.legionella.laatste_succes = dt_util.as_local(tijdstip)
        await self.store.bewaar_direct(self.engine_state)
        await self.async_request_refresh()

    async def veilige_stand(self) -> None:
        """Release everything we own (unload / master off)."""
        config = config_uit_options(dict(self.entry.options))
        s = self.engine_state
        await self.uitvoerder.veilige_stand(
            relais_uitzetten=bool(s and (s.warmwater_actief or s.legionella.forceer_actief)),
            ev_uitzetten=bool(s and s.ev_actief),
            feed_in_w=config.feed_in_herstel_w,
            ontlading_w=config.ontlading_herstel_w,
            setpoint_w=config.setpoint_idle_w,
        )
        if s:
            s.warmwater_actief = False
            s.ev_actief = False
            s.ev_ampere = 0
            s.netladen_actief = False
            await self.store.bewaar_direct(s)
