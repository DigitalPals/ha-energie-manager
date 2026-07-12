"""Feature-flag switches. Values live in entry.options."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import OPT_AUTOMATISCH_BEHEER
from .coordinator import EnergieManagerCoordinator
from .entity import EnergieManagerEntity


@dataclass(frozen=True, kw_only=True)
class VlagBeschrijving(SwitchEntityDescription):
    standaard: bool = False


VLAGGEN: tuple[VlagBeschrijving, ...] = (
    VlagBeschrijving(
        key=OPT_AUTOMATISCH_BEHEER,
        name="Automatisch beheer",
        icon="mdi:brain",
        standaard=False,
    ),
    VlagBeschrijving(
        key="warmwater_aan",
        name="Warmwater boost beheer",
        icon="mdi:water-boiler",
        standaard=True,
    ),
    VlagBeschrijving(
        key="ev_zon_aan",
        name="EV zonneladen beheer",
        icon="mdi:solar-power-variant",
        standaard=True,
    ),
    VlagBeschrijving(
        key="legionella_aan",
        name="Legionella bewaking",
        icon="mdi:bacteria-outline",
        standaard=True,
    ),
    VlagBeschrijving(
        key="negatieve_prijs_aan",
        name="Negatieve prijs bescherming",
        icon="mdi:transmission-tower-off",
        standaard=True,
    ),
    VlagBeschrijving(
        key="netladen_aan",
        name="Accu goedkoop laden",
        icon="mdi:battery-charging-outline",
        standaard=False,
    ),
    VlagBeschrijving(
        key="warmwater_goedkoop_aan",
        name="Warmwater op goedkope stroom",
        icon="mdi:currency-eur",
        standaard=False,
    ),
    VlagBeschrijving(
        key="ev_goedkoop_aan",
        name="EV goedkoop laden",
        icon="mdi:ev-station",
        standaard=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EnergieManagerCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = [VlagSwitch(coordinator, b) for b in VLAGGEN]
    entities.append(EvDirectLadenSwitch(coordinator))
    async_add_entities(entities)


class EvDirectLadenSwitch(EnergieManagerEntity, SwitchEntity):
    """Manual override: charge the EV now, until full or unplugged.

    Runtime state on the engine (not an options flag): it clears itself when
    the session ends, so every new session starts back in automatic mode.
    """

    _attr_name = "EV direct laden"
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: EnergieManagerCoordinator) -> None:
        super().__init__(coordinator, "ev_direct_laden")

    @property
    def is_on(self) -> bool:
        s = self.coordinator.engine_state
        return bool(s and s.ev_direct_laden)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.zet_ev_direct_laden(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.zet_ev_direct_laden(False)


class VlagSwitch(EnergieManagerEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    entity_description: VlagBeschrijving

    def __init__(
        self, coordinator: EnergieManagerCoordinator, beschrijving: VlagBeschrijving
    ) -> None:
        super().__init__(coordinator, beschrijving.key)
        self.entity_description = beschrijving

    @property
    def is_on(self) -> bool:
        return bool(
            self.coordinator.entry.options.get(
                self.entity_description.key, self.entity_description.standaard
            )
        )

    async def _zet(self, waarde: bool) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, self.entity_description.key: waarde}
        )
        # master off = one-time safe-state release of everything we own
        if self.entity_description.key == OPT_AUTOMATISCH_BEHEER and not waarde:
            await self.coordinator.veilige_stand()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._zet(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._zet(False)
