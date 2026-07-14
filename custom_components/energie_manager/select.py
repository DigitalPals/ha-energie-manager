"""Forced-mode select (dashboard convenience; 30 min per selection)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EnergieManagerCoordinator
from .core.model import Modus
from .entity import EnergieManagerEntity

_AUTOMATISCH = "automatisch"
_KEUZES = [
    _AUTOMATISCH,
    str(Modus.ZELFVERBRUIK),
    str(Modus.WARMWATER_BOOST),
    str(Modus.EV_LADEN),
    str(Modus.GOEDKOOP_LADEN),
    str(Modus.VOORKOELEN),
    str(Modus.BATTERIJ_BESCHERMEN),
]
_DUUR = timedelta(minutes=30)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([GeforceerdeModusSelect(entry.runtime_data)])


class GeforceerdeModusSelect(EnergieManagerEntity, SelectEntity):
    _attr_name = "Geforceerde modus"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:gesture-tap-button"
    _attr_options = _KEUZES

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "geforceerde_modus")

    @property
    def current_option(self) -> str:
        s = self.coordinator.engine_state
        if s is None or s.geforceerde_modus is None:
            return _AUTOMATISCH
        return str(s.geforceerde_modus)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.forceer_modus(
            None if option == _AUTOMATISCH else Modus(option), _DUUR
        )
        self.async_write_ha_state()
