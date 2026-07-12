"""Read-only sensors: mode, reason, surplus, legionella, price window."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import EnergieManagerCoordinator
from .core.model import Modus
from .core.prijs import goedkoopste_venster
from .entity import EnergieManagerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EnergieManagerCoordinator = entry.runtime_data
    async_add_entities(
        [
            ActieveModusSensor(coordinator),
            BesluitRedenSensor(coordinator),
            ZonneOverschotSensor(coordinator),
            LegionellaLaatsteSuccesSensor(coordinator),
            LegionellaDagenSensor(coordinator),
            VolgendeLegionellaRunSensor(coordinator),
            GoedkoopsteVensterSensor(coordinator),
            EvLaadstroomSensor(coordinator),
        ]
    )


class ActieveModusSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Actieve modus"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [str(m) for m in Modus]
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "actieve_modus")

    @property
    def native_value(self) -> str | None:
        return str(self.coordinator.data.modus) if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        besluit = self.coordinator.data
        s = self.coordinator.engine_state
        return {
            "reden": besluit.reden if besluit else None,
            "overlays": [str(o) for o in besluit.overlays] if besluit else [],
            "sinds": s.modus_sinds.isoformat() if s and s.modus_sinds else None,
            "dwell_tot": s.dwell_tot.isoformat() if s and s.dwell_tot else None,
            "automatisch_beheer": self.coordinator.automatisch_beheer,
            "laatste_besluiten": list(self.coordinator.geschiedenis)[:10],
        }


class BesluitRedenSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Besluit reden"
    _attr_icon = "mdi:head-question-outline"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "besluit_reden")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.reden[:255]


class ZonneOverschotSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Zonne-overschot"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "zonne_overschot")

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.overschot_kw


class LegionellaLaatsteSuccesSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Legionella laatste succes"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bacteria-outline"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "legionella_laatste_succes")

    @property
    def native_value(self) -> datetime | None:
        s = self.coordinator.engine_state
        if s is None or s.legionella.laatste_succes is None:
            return None
        return dt_util.as_utc(s.legionella.laatste_succes)


class LegionellaDagenSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Legionella dagen geleden"
    _attr_native_unit_of_measurement = "d"
    _attr_icon = "mdi:calendar-clock"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "legionella_dagen_geleden")

    @property
    def native_value(self) -> int | None:
        s = self.coordinator.engine_state
        if s is None or s.legionella.laatste_succes is None:
            return None
        return (dt_util.now() - s.legionella.laatste_succes).days


class VolgendeLegionellaRunSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Volgende legionella-run"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-arrow-right"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "volgende_legionella_run")

    @property
    def native_value(self) -> datetime | None:
        s = self.coordinator.engine_state
        if s is None or s.legionella.gepland_voor is None:
            return None
        return dt_util.as_utc(s.legionella.gepland_voor)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.coordinator.engine_state
        attrs: dict[str, Any] = {}
        if s and s.legionella.laatste_succes:
            attrs["deadline"] = (
                s.legionella.laatste_succes + timedelta(days=7)
            ).isoformat()
        if s:
            attrs["cyclus_actief"] = s.legionella.forceer_actief
        return attrs


class GoedkoopsteVensterSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "Goedkoopste uren"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:currency-eur"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "goedkoopste_uren_venster")
        self._venster = None

    def _bereken(self):
        invoer = self.coordinator.laatste_invoer
        if invoer is None:
            return None
        return goedkoopste_venster(invoer.prijs_slots, 1)

    @property
    def native_value(self) -> datetime | None:
        venster = self._bereken()
        return dt_util.as_utc(venster.start) if venster else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        venster = self._bereken()
        if venster is None:
            return {}
        return {
            "einde": venster.einde.isoformat(),
            "gemiddeld_tarief": round(venster.gemiddeld_tarief, 4),
            "slots": [
                {
                    "start": s.start.isoformat(),
                    "tarief": s.tarief,
                    "groep": s.groep,
                }
                for s in (self.coordinator.laatste_invoer.prijs_slots or ())
            ],
        }


class EvLaadstroomSensor(EnergieManagerEntity, SensorEntity):
    _attr_name = "EV laadstroom doel"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "ev_laadstroom_doel")

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.ev_ampere if self.coordinator.data.ev_actief else 0
