"""Render energie_manager_besluit events in the HA logbook."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, EVENT_BESLUIT, NAAM


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    @callback
    def _beschrijf(event: Event) -> dict[str, str]:
        data = event.data
        oud = data.get("oude_modus") or "start"
        nieuw = data.get("nieuwe_modus", "?")
        reden = data.get("reden", "")
        suffix = "" if data.get("uitgevoerd", True) else " [beheer uit]"
        return {
            LOGBOOK_ENTRY_NAME: NAAM,
            LOGBOOK_ENTRY_MESSAGE: f"{oud} → {nieuw} ({reden}){suffix}",
        }

    async_describe_event(DOMAIN, EVENT_BESLUIT, _beschrijf)
