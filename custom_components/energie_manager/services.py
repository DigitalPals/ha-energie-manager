"""Integration services."""

from __future__ import annotations

from datetime import timedelta

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .core.model import Modus

SERVICE_FORCEER_MODUS = "forceer_modus"
SERVICE_START_LEGIONELLA = "start_legionella"
SERVICE_FORCEER_EVALUATIE = "forceer_evaluatie"
SERVICE_ZET_LEGIONELLA_SUCCES = "zet_legionella_succes"

_AUTOMATISCH = "automatisch"
_MAX_DUUR = timedelta(hours=8)

_FORCEER_SCHEMA = vol.Schema(
    {
        vol.Required("modus"): vol.In(
            [_AUTOMATISCH]
            + [
                str(m)
                for m in (
                    Modus.ZELFVERBRUIK,
                    Modus.WARMWATER_BOOST,
                    Modus.EV_LADEN,
                    Modus.GOEDKOOP_LADEN,
                    Modus.BATTERIJ_BESCHERMEN,
                )
            ]
        ),
        vol.Optional("duur", default={"minutes": 30}): vol.All(
            cv.time_period, vol.Range(min=timedelta(minutes=1), max=_MAX_DUUR)
        ),
    }
)

_SUCCES_SCHEMA = vol.Schema({vol.Required("tijdstip"): cv.datetime})


def _coordinator(hass: HomeAssistant):
    entries = hass.config_entries.async_entries(DOMAIN)
    for entry in entries:
        if getattr(entry, "runtime_data", None) is not None:
            return entry.runtime_data
    raise HomeAssistantError("Energie Manager is niet geconfigureerd")


def async_registreer_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_FORCEER_MODUS):
        return

    async def _forceer_modus(call: ServiceCall) -> None:
        coordinator = _coordinator(hass)
        modus = call.data["modus"]
        await coordinator.forceer_modus(
            None if modus == _AUTOMATISCH else Modus(modus), call.data["duur"]
        )

    async def _start_legionella(call: ServiceCall) -> None:
        await _coordinator(hass).start_legionella()

    async def _forceer_evaluatie(call: ServiceCall) -> None:
        await _coordinator(hass).async_request_refresh()

    async def _zet_succes(call: ServiceCall) -> None:
        await _coordinator(hass).zet_legionella_succes(call.data["tijdstip"])

    hass.services.async_register(
        DOMAIN, SERVICE_FORCEER_MODUS, _forceer_modus, schema=_FORCEER_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_START_LEGIONELLA, _start_legionella)
    hass.services.async_register(DOMAIN, SERVICE_FORCEER_EVALUATIE, _forceer_evaluatie)
    hass.services.async_register(
        DOMAIN, SERVICE_ZET_LEGIONELLA_SUCCES, _zet_succes, schema=_SUCCES_SCHEMA
    )
