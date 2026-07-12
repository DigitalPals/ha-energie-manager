"""Action buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EnergieManagerCoordinator
from .entity import EnergieManagerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EnergieManagerCoordinator = entry.runtime_data
    async_add_entities(
        [StartLegionellaButton(coordinator), ForceerEvaluatieButton(coordinator)]
    )


class StartLegionellaButton(EnergieManagerEntity, ButtonEntity):
    _attr_name = "Start legionella-cyclus nu"
    _attr_icon = "mdi:play-circle"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "start_legionella_nu")

    async def async_press(self) -> None:
        await self.coordinator.start_legionella()


class ForceerEvaluatieButton(EnergieManagerEntity, ButtonEntity):
    _attr_name = "Forceer evaluatie"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "forceer_evaluatie")

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
