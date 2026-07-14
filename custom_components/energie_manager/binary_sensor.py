"""Binary sensors: overlay, channel activity, input health."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EnergieManagerCoordinator
from .core.model import Overlay
from .entity import EnergieManagerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EnergieManagerCoordinator = entry.runtime_data
    async_add_entities(
        [
            NegatievePrijsBinarySensor(coordinator),
            WarmwaterBoostBinarySensor(coordinator),
            EvLadenBinarySensor(coordinator),
            LegionellaBezigBinarySensor(coordinator),
            InvoerVerouderdBinarySensor(coordinator),
            VoorkoelenBinarySensor(coordinator),
            PiekVasthoudenBinarySensor(coordinator),
            PiekOntladenBinarySensor(coordinator),
        ]
    )


class NegatievePrijsBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Negatieve prijs actief"
    _attr_icon = "mdi:transmission-tower-off"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "negatieve_prijs_actief")

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return Overlay.NEGATIEVE_PRIJS in self.coordinator.data.overlays


class WarmwaterBoostBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Warmwater boost actief"
    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "warmwater_boost_actief")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.warmwater_actief if self.coordinator.data else None


class EvLadenBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "EV laden actief"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "ev_laden_actief")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.ev_actief if self.coordinator.data else None


class LegionellaBezigBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Legionella bezig"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:bacteria"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "legionella_bezig")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.legionella_bezig if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        return {
            "hold_minuten": round(self.coordinator.data.legionella_hold_minuten, 1)
        }


class VoorkoelenBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Voorkoelen actief"
    _attr_device_class = BinarySensorDeviceClass.COLD
    _attr_icon = "mdi:snowflake"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "voorkoelen_actief")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.voorkoelen_actief if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        invoer = self.coordinator.laatste_invoer
        if invoer is None:
            return {}
        return {
            "binnen_temp": invoer.binnen_temp,
            "buiten_temp": invoer.buiten_temp,
            "dauwpunt_marge": invoer.dauwpunt_marge_c,
        }


class PiekVasthoudenBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Piek vasthouden actief"
    _attr_icon = "mdi:battery-lock"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "piek_vasthouden_actief")

    @property
    def is_on(self) -> bool | None:
        return (
            self.coordinator.data.piek_vasthouden_actief
            if self.coordinator.data
            else None
        )


class PiekOntladenBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Piek teruglevering actief"
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "piek_ontladen_actief")

    @property
    def is_on(self) -> bool | None:
        return (
            self.coordinator.data.piek_export_actief if self.coordinator.data else None
        )


class InvoerVerouderdBinarySensor(EnergieManagerEntity, BinarySensorEntity):
    _attr_name = "Invoer verouderd"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "invoer_verouderd")

    @property
    def is_on(self) -> bool | None:
        invoer = self.coordinator.laatste_invoer
        return bool(invoer.verouderd) if invoer is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        invoer = self.coordinator.laatste_invoer
        return {
            "sensoren": list(invoer.verouderd) if invoer else [],
            "lang_ongewijzigd": list(self.coordinator.lang_ongewijzigd),
            "commando_fouten": self.coordinator.uitvoerder.fouten,
        }
