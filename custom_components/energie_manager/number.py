"""Tunable numbers. Values live in entry.options (core Config field names)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONFIG_VELDEN
from .coordinator import EnergieManagerCoordinator
from .entity import EnergieManagerEntity

_EUR_KWH = f"{CURRENCY_EURO}/kWh"
_KW = UnitOfPower.KILO_WATT
_W = UnitOfPower.WATT
_PCT = PERCENTAGE
_C = UnitOfTemperature.CELSIUS
_A = UnitOfElectricCurrent.AMPERE
_S = UnitOfTime.SECONDS
_H = UnitOfTime.HOURS
_KWH = "kWh"


@dataclass(frozen=True, kw_only=True)
class TunableBeschrijving(NumberEntityDescription):
    """Description; key == core Config field name."""


TUNABLES: tuple[TunableBeschrijving, ...] = (
    TunableBeschrijving(
        key="overschot_drempel_kw", name="Overschot drempel warmwater",
        native_min_value=0.5, native_max_value=10.0, native_step=0.1,
        native_unit_of_measurement=_KW, icon="mdi:solar-power",
    ),
    TunableBeschrijving(
        key="uitschakel_drempel_kw", name="Overschot uitschakeldrempel",
        native_min_value=0.1, native_max_value=5.0, native_step=0.1,
        native_unit_of_measurement=_KW, icon="mdi:solar-power",
    ),
    TunableBeschrijving(
        key="boiler_doel_c", name="Boiler doeltemperatuur",
        native_min_value=55.0, native_max_value=65.0, native_step=0.5,
        native_unit_of_measurement=_C, icon="mdi:thermometer-high",
    ),
    TunableBeschrijving(
        key="boiler_comfortvloer_c", name="Boiler comfortvloer",
        native_min_value=30.0, native_max_value=60.0, native_step=1.0,
        native_unit_of_measurement=_C, icon="mdi:thermometer-low",
    ),
    TunableBeschrijving(
        key="batterij_prioriteit_soc", name="Batterij prioriteit SoC",
        native_min_value=50.0, native_max_value=100.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-90",
    ),
    TunableBeschrijving(
        key="warmwater_soc_uitschakel", name="Warmwater SoC uitschakelgrens",
        native_min_value=50.0, native_max_value=100.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-alert",
    ),
    TunableBeschrijving(
        key="batterij_reserve_soc", name="Batterij reserve SoC",
        native_min_value=10.0, native_max_value=50.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-30",
    ),
    TunableBeschrijving(
        key="noodreserve_soc", name="Noodreserve SoC",
        native_min_value=5.0, native_max_value=25.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-10",
    ),
    TunableBeschrijving(
        key="ev_start_soc", name="EV start SoC",
        native_min_value=20.0, native_max_value=60.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-50",
    ),
    TunableBeschrijving(
        key="ev_vaste_ampere", name="EV vaste laadstroom (goedkoop)",
        native_min_value=6, native_max_value=32, native_step=1,
        native_unit_of_measurement=_A, icon="mdi:current-ac",
    ),
    TunableBeschrijving(
        key="prijsplafond_batterij", name="Prijsplafond accu netladen",
        native_min_value=-0.50, native_max_value=0.50, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:currency-eur",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="prijsplafond_warmwater", name="Prijsplafond warmwater",
        native_min_value=-0.50, native_max_value=0.50, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:currency-eur",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="prijsplafond_ev", name="Prijsplafond EV laden",
        native_min_value=-0.50, native_max_value=0.50, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:currency-eur",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="doel_soc_netladen", name="Doel SoC accu netladen",
        native_min_value=30.0, native_max_value=100.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-60",
    ),
    TunableBeschrijving(
        key="max_laadvermogen_net_w", name="Max laadvermogen net",
        native_min_value=500.0, native_max_value=5000.0, native_step=100.0,
        native_unit_of_measurement=_W, icon="mdi:transmission-tower-import",
    ),
    TunableBeschrijving(
        key="max_netladen_uren_per_dag", name="Max netladen uren per dag",
        native_min_value=0.0, native_max_value=8.0, native_step=0.5,
        native_unit_of_measurement=_H, icon="mdi:timer-sand",
    ),
    TunableBeschrijving(
        key="zon_slecht_drempel_kwh", name="Zonprognose drempel netladen",
        native_min_value=0.0, native_max_value=40.0, native_step=1.0,
        native_unit_of_measurement=_KWH, icon="mdi:weather-partly-cloudy",
    ),
    TunableBeschrijving(
        key="dwell_s", name="Minimale modusduur",
        native_min_value=60.0, native_max_value=1800.0, native_step=30.0,
        native_unit_of_measurement=_S, icon="mdi:timer-outline",
    ),
    # --- arbitrage ---
    TunableBeschrijving(
        key="batterij_capaciteit_kwh", name="Batterijcapaciteit",
        native_min_value=10.0, native_max_value=100.0, native_step=1.0,
        native_unit_of_measurement=_KWH, icon="mdi:battery-high",
    ),
    TunableBeschrijving(
        key="rendement_rondrit", name="Rendement laad-ontlaadcyclus",
        native_min_value=0.70, native_max_value=1.00, native_step=0.01,
        icon="mdi:battery-sync",
    ),
    TunableBeschrijving(
        key="huis_basislast_kw", name="Basislast woning",
        native_min_value=0.2, native_max_value=5.0, native_step=0.1,
        native_unit_of_measurement=_KW, icon="mdi:home-lightning-bolt",
    ),
    TunableBeschrijving(
        key="piek_drempel_eur", name="Piekdrempel tarief",
        native_min_value=0.0, native_max_value=1.0, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:chart-bell-curve",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="arbitrage_min_marge", name="Arbitrage minimale marge",
        native_min_value=0.0, native_max_value=0.50, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:cash-plus",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="export_bodem_eur", name="Teruglever bodemtarief",
        native_min_value=0.0, native_max_value=1.0, native_step=0.01,
        native_unit_of_measurement=_EUR_KWH, icon="mdi:cash-check",
        mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="piek_reserve_soc", name="Piekreserve SoC",
        native_min_value=10.0, native_max_value=80.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-lock",
    ),
    TunableBeschrijving(
        key="doel_soc_piek", name="Doel SoC voorladen piek",
        native_min_value=50.0, native_max_value=100.0, native_step=1.0,
        native_unit_of_measurement=_PCT, icon="mdi:battery-charging-90",
    ),
    TunableBeschrijving(
        key="max_export_w", name="Max terugleveren vermogen",
        native_min_value=0.0, native_max_value=5000.0, native_step=100.0,
        native_unit_of_measurement=_W, icon="mdi:transmission-tower-export",
    ),
    TunableBeschrijving(
        key="max_export_uren_per_dag", name="Max terugleveruren per dag",
        native_min_value=0.0, native_max_value=8.0, native_step=0.5,
        native_unit_of_measurement=_H, icon="mdi:timer-sand",
    ),
    TunableBeschrijving(
        key="zon_einde_uur", name="Einde zonuren",
        native_min_value=16, native_max_value=23, native_step=1,
        icon="mdi:weather-sunset-down",
    ),
    # --- voorkoelen ---
    TunableBeschrijving(
        key="voorkoelen_drempel_kw", name="Overschot drempel voorkoelen",
        native_min_value=0.5, native_max_value=10.0, native_step=0.1,
        native_unit_of_measurement=_KW, icon="mdi:snowflake",
    ),
    TunableBeschrijving(
        key="voorkoelen_uitschakel_kw", name="Voorkoelen uitschakeldrempel",
        native_min_value=0.1, native_max_value=5.0, native_step=0.1,
        native_unit_of_measurement=_KW, icon="mdi:snowflake-off",
    ),
    TunableBeschrijving(
        key="voorkoelen_vloer_c", name="Voorkoelen comfortvloer",
        native_min_value=18.0, native_max_value=24.0, native_step=0.5,
        native_unit_of_measurement=_C, icon="mdi:thermometer-low",
    ),
    TunableBeschrijving(
        key="voorkoelen_buiten_min_c", name="Voorkoelen buitentemperatuur minimum",
        native_min_value=10.0, native_max_value=30.0, native_step=0.5,
        native_unit_of_measurement=_C, icon="mdi:sun-thermometer",
    ),
    TunableBeschrijving(
        key="voorkoelen_offset", name="Voorkoelen koelcurve-offset",
        native_min_value=-10.0, native_max_value=0.0, native_step=1.0,
        icon="mdi:tune-vertical", mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="koel_offset_herstel", name="Koelcurve-offset herstelwaarde",
        native_min_value=-10.0, native_max_value=10.0, native_step=1.0,
        icon="mdi:tune", mode=NumberMode.BOX,
    ),
    TunableBeschrijving(
        key="dauwpunt_marge_min_c", name="Dauwpunt marge minimum",
        native_min_value=0.0, native_max_value=5.0, native_step=0.5,
        native_unit_of_measurement=_C, icon="mdi:water-percent-alert",
    ),
    TunableBeschrijving(
        key="voorkoelen_dwell_s", name="Voorkoelen minimale modusduur",
        native_min_value=600.0, native_max_value=3600.0, native_step=60.0,
        native_unit_of_measurement=_S, icon="mdi:timer-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: EnergieManagerCoordinator = entry.runtime_data
    async_add_entities(TunableNumber(coordinator, b) for b in TUNABLES)


class TunableNumber(EnergieManagerEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    entity_description: TunableBeschrijving

    def __init__(
        self, coordinator: EnergieManagerCoordinator, beschrijving: TunableBeschrijving
    ) -> None:
        super().__init__(coordinator, beschrijving.key)
        self.entity_description = beschrijving

    @property
    def native_value(self) -> float:
        sleutel = self.entity_description.key
        return float(
            self.coordinator.entry.options.get(sleutel, CONFIG_VELDEN[sleutel])
        )

    async def async_set_native_value(self, value: float) -> None:
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, self.entity_description.key: value},
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
