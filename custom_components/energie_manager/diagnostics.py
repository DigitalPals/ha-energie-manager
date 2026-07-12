"""Diagnostics download: config, engine state, decision history."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .store import state_naar_dict


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    besluit = coordinator.data
    return {
        "mapping": dict(entry.data),
        "options": dict(entry.options),
        "engine_state": state_naar_dict(coordinator.engine_state)
        if coordinator.engine_state
        else None,
        "laatste_besluit": {
            "modus": str(besluit.modus),
            "overlays": [str(o) for o in besluit.overlays],
            "reden": besluit.reden,
            "commandos": [
                {"doel": str(c.doel), "waarde": c.waarde, "reden": c.reden}
                for c in besluit.commandos
            ],
        }
        if besluit
        else None,
        "laatste_invoer": asdict(coordinator.laatste_invoer)
        if coordinator.laatste_invoer
        else None,
        "geschiedenis": list(coordinator.geschiedenis),
        "commando_fouten": coordinator.uitvoerder.fouten,
        "automatisch_beheer": coordinator.automatisch_beheer,
    }
