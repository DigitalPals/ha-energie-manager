"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAAM
from .coordinator import EnergieManagerCoordinator


class EnergieManagerEntity(CoordinatorEntity[EnergieManagerCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EnergieManagerCoordinator, sleutel: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{sleutel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=NAAM,
            manufacturer="digitalbrain",
            model="Energie Manager",
        )
