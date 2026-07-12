"""Config flow: entity mapping only. Tunables live on config entities."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AC_VERBRUIK,
    CONF_BATTERIJ_SOC,
    CONF_BATTERIJ_VERMOGEN,
    CONF_BOILER_TEMPERATUUR,
    CONF_DOEL,
    CONF_EV_SESSIE_ENERGIE,
    CONF_EV_STATUS_RAW,
    CONF_EV_VERMOGEN,
    CONF_FORECAST_GROEP_PATROON,
    CONF_FORECAST_TARIEF_PATROON,
    CONF_NET_VERMOGEN,
    CONF_OVERSCHOT_EXTERN,
    CONF_PV_VERMOGEN,
    CONF_TARIEF,
    CONF_ZON_MORGEN,
    CONF_ZON_VANDAAG,
    DOMAIN,
    MAPPING_DEFAULTS,
    NAAM,
)
from .core.model import Doel

_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor")
)
_SENSOR_MULTI = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", multiple=True)
)
_SWITCH = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="switch")
)
_NUMBER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="number")
)

_INVOER_VELDEN: dict[str, Any] = {
    CONF_PV_VERMOGEN: _SENSOR,
    CONF_AC_VERBRUIK: _SENSOR,
    CONF_BATTERIJ_VERMOGEN: _SENSOR,
    CONF_BATTERIJ_SOC: _SENSOR,
    CONF_BOILER_TEMPERATUUR: _SENSOR,
    CONF_EV_STATUS_RAW: _SENSOR,
    CONF_EV_VERMOGEN: _SENSOR,
    CONF_EV_SESSIE_ENERGIE: _SENSOR,
    CONF_NET_VERMOGEN: _SENSOR,
    CONF_TARIEF: _SENSOR,
}

_UITVOER_VELDEN: dict[str, Any] = {
    CONF_DOEL[Doel.WARMWATER_RELAIS]: _SWITCH,
    CONF_DOEL[Doel.EV_SCHAKELAAR]: _SWITCH,
    CONF_DOEL[Doel.EV_STROOM]: _NUMBER,
    CONF_DOEL[Doel.FEED_IN]: _NUMBER,
    CONF_DOEL[Doel.MAX_ONTLADING]: _NUMBER,
    CONF_DOEL[Doel.NET_SETPOINT]: _NUMBER,
    CONF_DOEL[Doel.SOLAR_LIMIET_1]: _NUMBER,
    CONF_DOEL[Doel.SOLAR_LIMIET_2]: _NUMBER,
}


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    velden: dict[Any, Any] = {}
    for sleutel, kiezer in {**_INVOER_VELDEN, **_UITVOER_VELDEN}.items():
        velden[vol.Required(sleutel, default=defaults.get(sleutel, ""))] = kiezer
    velden[vol.Optional(CONF_ZON_VANDAAG, default=defaults.get(CONF_ZON_VANDAAG, []))] = (
        _SENSOR_MULTI
    )
    velden[vol.Optional(CONF_ZON_MORGEN, default=defaults.get(CONF_ZON_MORGEN, []))] = (
        _SENSOR_MULTI
    )
    if defaults.get(CONF_OVERSCHOT_EXTERN):
        velden[
            vol.Optional(CONF_OVERSCHOT_EXTERN, default=defaults[CONF_OVERSCHOT_EXTERN])
        ] = _SENSOR
    else:
        velden[vol.Optional(CONF_OVERSCHOT_EXTERN)] = _SENSOR
    velden[
        vol.Optional(
            CONF_FORECAST_TARIEF_PATROON,
            default=defaults.get(CONF_FORECAST_TARIEF_PATROON, ""),
        )
    ] = str
    velden[
        vol.Optional(
            CONF_FORECAST_GROEP_PATROON,
            default=defaults.get(CONF_FORECAST_GROEP_PATROON, ""),
        )
    ] = str
    return vol.Schema(velden)


def _valideer(hass, invoer: dict[str, Any]) -> dict[str, str]:
    """Every mapped single entity must exist."""
    fouten: dict[str, str] = {}
    for sleutel in {**_INVOER_VELDEN, **_UITVOER_VELDEN}:
        entity_id = invoer.get(sleutel)
        if entity_id and hass.states.get(entity_id) is None:
            fouten[sleutel] = "entiteit_onbekend"
    return fouten


class EnergieManagerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        fouten: dict[str, str] = {}
        if user_input is not None:
            fouten = _valideer(self.hass, user_input)
            if not fouten:
                return self.async_create_entry(title=NAAM, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(dict(MAPPING_DEFAULTS)),
            errors=fouten,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> EnergieManagerOptionsFlow:
        return EnergieManagerOptionsFlow()


class EnergieManagerOptionsFlow(OptionsFlow):
    """Remap entities. Tunables are config entities, not options-flow fields."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        fouten: dict[str, str] = {}
        if user_input is not None:
            fouten = _valideer(self.hass, user_input)
            if not fouten:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, **user_input}
                )
                return self.async_create_entry(title="", data=dict(self.config_entry.options))

        defaults = {**MAPPING_DEFAULTS, **self.config_entry.data}
        return self.async_show_form(
            step_id="init", data_schema=_schema(defaults), errors=fouten
        )
