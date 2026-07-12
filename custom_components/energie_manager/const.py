"""Constants for the Energie Manager integration."""

from __future__ import annotations

from dataclasses import fields

from .core.model import Config, Doel

DOMAIN = "energie_manager"
NAAM = "Energie Manager"

UPDATE_INTERVAL_S = 30
EVENT_BESLUIT = "energie_manager_besluit"

# ------------------------------------------------------------------ #
# Entity mapping (config flow data). Keys double as translation keys. #
# ------------------------------------------------------------------ #

# inputs
CONF_PV_VERMOGEN = "pv_vermogen"
CONF_AC_VERBRUIK = "ac_verbruik"
CONF_BATTERIJ_VERMOGEN = "batterij_vermogen"
CONF_BATTERIJ_SOC = "batterij_soc"
CONF_BOILER_TEMPERATUUR = "boiler_temperatuur"
CONF_EV_STATUS_RAW = "ev_status_raw"
CONF_EV_VERMOGEN = "ev_vermogen"
CONF_TARIEF = "tarief"
CONF_FORECAST_TARIEF_PATROON = "forecast_tarief_patroon"
CONF_FORECAST_GROEP_PATROON = "forecast_groep_patroon"
CONF_ZON_VANDAAG = "zon_vandaag"  # multi-entity, summed (kWh remaining today)
CONF_ZON_MORGEN = "zon_morgen"  # multi-entity, summed (kWh tomorrow)
CONF_OVERSCHOT_EXTERN = "overschot_extern"  # optional override sensor

# outputs (map 1:1 to core Doel)
CONF_DOEL: dict[Doel, str] = {
    Doel.WARMWATER_RELAIS: "warmwater_relais",
    Doel.EV_SCHAKELAAR: "ev_schakelaar",
    Doel.EV_STROOM: "ev_stroom",
    Doel.FEED_IN: "feed_in",
    Doel.MAX_ONTLADING: "max_ontlading",
    Doel.NET_SETPOINT: "net_setpoint",
    Doel.SOLAR_LIMIET_1: "solar_limiet_1",
    Doel.SOLAR_LIMIET_2: "solar_limiet_2",
}

# Pre-filled defaults for John's installation; every field remappable.
MAPPING_DEFAULTS: dict[str, str | list[str]] = {
    CONF_PV_VERMOGEN: "sensor.victron_pv_power_total",
    CONF_AC_VERBRUIK: "sensor.victron_ac_loads_total",
    CONF_BATTERIJ_VERMOGEN: "sensor.victron_battery_power",
    CONF_BATTERIJ_SOC: "sensor.victron_battery_soc",
    CONF_BOILER_TEMPERATUUR: "sensor.bt7_hw_top_40013",
    CONF_EV_STATUS_RAW: "sensor.evcs_status_raw",
    CONF_EV_VERMOGEN: "sensor.evcs_total_power",
    CONF_TARIEF: "sensor.zonneplan_current_electricity_tariff",
    CONF_FORECAST_TARIEF_PATROON: "sensor.zonneplan_forecast_tariff_hour_{n}",
    CONF_FORECAST_GROEP_PATROON: "sensor.zonneplan_forecast_tariff_group_hour_{n}",
    CONF_ZON_VANDAAG: [
        "sensor.energy_production_today_remaining",
        "sensor.energy_production_today_remaining_2",
        "sensor.energy_production_today_remaining_3",
    ],
    CONF_ZON_MORGEN: [
        "sensor.energy_production_tomorrow",
        "sensor.energy_production_tomorrow_2",
        "sensor.energy_production_tomorrow_3",
    ],
    CONF_OVERSCHOT_EXTERN: "",
    CONF_DOEL[Doel.WARMWATER_RELAIS]: "switch.shellypro1_ac15186d9688_switch_0",
    CONF_DOEL[Doel.EV_SCHAKELAAR]: "switch.evcs_charging",
    CONF_DOEL[Doel.EV_STROOM]: "number.evcs_charging_current",
    CONF_DOEL[Doel.FEED_IN]: "number.victron_ess_max_feed_in_power_control",
    CONF_DOEL[Doel.MAX_ONTLADING]: "number.victron_ess_max_discharge_power_control",
    CONF_DOEL[Doel.NET_SETPOINT]: "number.victron_ess_grid_setpoint_control",
    CONF_DOEL[Doel.SOLAR_LIMIET_1]: "number.solar_schuilstal_active_power_limit_control",
    CONF_DOEL[Doel.SOLAR_LIMIET_2]: "number.solar_stallen_active_power_limit_control",
}

FORECAST_UREN = 8

# ------------------------------------------------------------------ #
# Options: master switch + every core Config field (same names).      #
# ------------------------------------------------------------------ #

OPT_AUTOMATISCH_BEHEER = "automatisch_beheer"

# Feature-flag option keys are the Config field names (warmwater_aan, ...).
CONFIG_VELDEN = {f.name: f.default for f in fields(Config)}


def config_uit_options(options: dict) -> Config:
    """Build the core Config from entry options (missing keys -> defaults)."""
    waarden = {
        naam: options.get(naam, standaard)
        for naam, standaard in CONFIG_VELDEN.items()
    }
    return Config(**waarden)


# staleness thresholds per input class (seconds, on state.last_reported)
MAX_LEEFTIJD_S: dict[str, float] = {
    CONF_PV_VERMOGEN: 300,
    CONF_AC_VERBRUIK: 300,
    CONF_BATTERIJ_VERMOGEN: 300,
    CONF_BATTERIJ_SOC: 600,
    CONF_BOILER_TEMPERATUUR: 1800,
    CONF_EV_STATUS_RAW: 300,
    CONF_EV_VERMOGEN: 300,
    CONF_TARIEF: 7200,
    CONF_OVERSCHOT_EXTERN: 300,
}

# migration source for the legionella timestamp
MIGRATIE_INPUT_DATETIME = "input_datetime.legionella_laatste_succes"
