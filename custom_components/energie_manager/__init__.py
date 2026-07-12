"""Energie Manager: the energy brain for this house."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_EV_SESSIE_ENERGIE,
    CONF_NET_VERMOGEN,
    MAPPING_DEFAULTS,
    MIGRATIE_INPUT_DATETIME,
)
from .coordinator import EnergieManagerCoordinator
from .executor import Uitvoerder
from .services import async_registreer_services
from .store import EnergieManagerStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type EnergieManagerConfigEntry = ConfigEntry[EnergieManagerCoordinator]


async def async_migrate_entry(
    hass: HomeAssistant, entry: EnergieManagerConfigEntry
) -> bool:
    """Backfill mapping keys added after the entry was created."""
    if entry.version == 1:
        data = dict(entry.data)
        for sleutel in (CONF_EV_SESSIE_ENERGIE, CONF_NET_VERMOGEN):
            data.setdefault(sleutel, MAPPING_DEFAULTS[sleutel])
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("Config entry gemigreerd naar versie 2 (EV-sessie invoer)")
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: EnergieManagerConfigEntry
) -> bool:
    store = EnergieManagerStore(hass, entry.entry_id)
    uitvoerder = Uitvoerder(hass, dict(entry.data))
    coordinator = EnergieManagerCoordinator(hass, entry, store, uitvoerder)
    await coordinator.async_initialiseer()

    # one-time migration: seed the legionella timestamp from the old helper
    if coordinator.engine_state.legionella.laatste_succes is None:
        oud = hass.states.get(MIGRATIE_INPUT_DATETIME)
        if oud is not None and oud.state not in ("unknown", "unavailable", ""):
            tijdstip = dt_util.parse_datetime(oud.state)
            if tijdstip is None:
                tijdstip = dt_util.parse_datetime(f"{oud.state}+00:00")
            if tijdstip is not None:
                coordinator.engine_state.legionella.laatste_succes = dt_util.as_local(
                    tijdstip
                )
                await store.bewaar_direct(coordinator.engine_state)
                _LOGGER.info(
                    "Legionella laatste succes overgenomen uit %s: %s",
                    MIGRATIE_INPUT_DATETIME,
                    oud.state,
                )

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_registreer_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: EnergieManagerConfigEntry
) -> None:
    """Reload only when the entity MAPPING changes.

    Tunables also live on this entry (options) and change often via the
    config entities; those are picked up on the next tick without a reload
    (a reload would run the unload safe-state and drop an active boost).
    """
    coordinator = entry.runtime_data
    if dict(entry.data) != coordinator.mapping_snapshot:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: EnergieManagerConfigEntry
) -> bool:
    coordinator = entry.runtime_data
    # release everything we own before going dark
    try:
        await coordinator.veilige_stand()
    except Exception:  # noqa: BLE001 - never block unload on safe-state errors
        _LOGGER.exception("Veilige stand bij unload mislukt")
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
