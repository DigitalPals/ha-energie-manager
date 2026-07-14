"""Integration tests: states in -> service calls out."""

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.energie_manager.const import (
    DOMAIN,
    MAPPING_DEFAULTS,
    OPT_AUTOMATISCH_BEHEER,
)
from custom_components.energie_manager.store import state_naar_dict, state_uit_dict

RELAIS = MAPPING_DEFAULTS["warmwater_relais"]
EV_SCHAKELAAR = MAPPING_DEFAULTS["ev_schakelaar"]


@pytest.fixture(autouse=True)
def _auto_enable(enable_custom_integrations):
    yield


def zet_states(hass, **over):
    """A sunny afternoon: boost-worthy surplus, everything else idle."""
    waarden = {
        MAPPING_DEFAULTS["pv_vermogen"]: "6000",
        MAPPING_DEFAULTS["ac_verbruik"]: "1000",
        MAPPING_DEFAULTS["batterij_vermogen"]: "0",
        MAPPING_DEFAULTS["batterij_soc"]: "96",
        MAPPING_DEFAULTS["boiler_temperatuur"]: "45",
        MAPPING_DEFAULTS["ev_status_raw"]: "0",
        MAPPING_DEFAULTS["ev_vermogen"]: "0",
        MAPPING_DEFAULTS["ev_sessie_energie"]: "0.00",
        MAPPING_DEFAULTS["net_vermogen"]: "0",
        MAPPING_DEFAULTS["tarief"]: "0.20",
        RELAIS: "off",
        EV_SCHAKELAAR: "off",
        MAPPING_DEFAULTS["ev_stroom"]: "6",
        MAPPING_DEFAULTS["feed_in"]: "5000",
        MAPPING_DEFAULTS["max_ontlading"]: "5000",
        MAPPING_DEFAULTS["net_setpoint"]: "50",
        MAPPING_DEFAULTS["solar_limiet_1"]: "100",
        MAPPING_DEFAULTS["solar_limiet_2"]: "100",
    }
    waarden.update(over)
    for entity_id, waarde in waarden.items():
        hass.states.async_set(entity_id, waarde)


def maak_entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Energie Manager",
        unique_id=DOMAIN,
        data=dict(MAPPING_DEFAULTS),
        options=options,
    )


async def _setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_tick_zet_boost_aan(hass):
    zet_states(hass)
    aan = async_mock_service(hass, "switch", "turn_on")
    uit = async_mock_service(hass, "switch", "turn_off")
    setw = async_mock_service(hass, "number", "set_value")

    entry = maak_entry(**{OPT_AUTOMATISCH_BEHEER: True})
    await _setup(hass, entry)

    # only the boost relay needed a write; the ESS numbers were already right
    assert len(aan) == 1
    assert aan[0].data["entity_id"] == RELAIS
    assert not uit
    assert not setw

    coordinator = entry.runtime_data
    assert coordinator.data.warmwater_actief
    modus = hass.states.get("sensor.energie_manager_actieve_modus")
    assert modus is not None
    assert modus.state == "warmwater_boost"


async def test_dedup_geen_dubbele_calls(hass):
    zet_states(hass)
    aan = async_mock_service(hass, "switch", "turn_on")

    entry = maak_entry(**{OPT_AUTOMATISCH_BEHEER: True})
    await _setup(hass, entry)
    assert len(aan) == 1

    # NB: platform setup replaced the domain mocks with the real switch
    # services; re-register mocks to observe post-setup call attempts.
    aan2 = async_mock_service(hass, "switch", "turn_on")
    uit2 = async_mock_service(hass, "switch", "turn_off")
    setw2 = async_mock_service(hass, "number", "set_value")

    # relay now reports on: identical second tick issues zero new calls
    hass.states.async_set(RELAIS, "on")
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not aan2 and not uit2 and not setw2


async def test_master_uit_geen_calls(hass):
    zet_states(hass)
    aan = async_mock_service(hass, "switch", "turn_on")
    uit = async_mock_service(hass, "switch", "turn_off")
    setw = async_mock_service(hass, "number", "set_value")

    entry = maak_entry()  # automatisch_beheer default: off
    await _setup(hass, entry)

    assert not aan and not uit and not setw
    coordinator = entry.runtime_data
    # decision is still computed and visible
    assert coordinator.data.warmwater_actief
    assert not coordinator.automatisch_beheer


async def test_unload_veilige_stand(hass):
    zet_states(hass)
    async_mock_service(hass, "switch", "turn_on")

    entry = maak_entry(**{OPT_AUTOMATISCH_BEHEER: True})
    await _setup(hass, entry)
    hass.states.async_set(RELAIS, "on")  # relay is ours and on

    # re-mock after platform setup (see test_dedup for why)
    uit = async_mock_service(hass, "switch", "turn_off")
    async_mock_service(hass, "number", "set_value")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert any(call.data["entity_id"] == RELAIS for call in uit)


async def test_veilige_terugval_bij_onbeschikbare_invoer(hass):
    zet_states(hass, **{MAPPING_DEFAULTS["pv_vermogen"]: "unavailable"})
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    async_mock_service(hass, "number", "set_value")

    entry = maak_entry(**{OPT_AUTOMATISCH_BEHEER: True})
    await _setup(hass, entry)

    modus = hass.states.get("sensor.energie_manager_actieve_modus")
    assert modus.state == "veilige_terugval"
    probleem = hass.states.get("binary_sensor.energie_manager_invoer_verouderd")
    assert probleem.state == "on"


async def test_ongewijzigde_invoer_is_niet_verouderd(hass, freezer):
    # Constant values (e.g. SoC behind a template proxy that only re-reports
    # on change) must NOT trip veilige_terugval; age is informational only.
    zet_states(hass)
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    async_mock_service(hass, "number", "set_value")

    entry = maak_entry(**{OPT_AUTOMATISCH_BEHEER: True})
    await _setup(hass, entry)

    freezer.tick(700)  # beyond every MAX_LEEFTIJD_S threshold (SoC = 600 s)
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    modus = hass.states.get("sensor.energie_manager_actieve_modus")
    assert modus.state != "veilige_terugval"
    probleem = hass.states.get("binary_sensor.energie_manager_invoer_verouderd")
    assert probleem.state == "off"
    assert any(
        naam.startswith("batterij_soc")
        for naam in probleem.attributes["lang_ongewijzigd"]
    )


async def test_legionella_migratie_uit_input_datetime(hass):
    zet_states(hass)
    async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set(
        "input_datetime.legionella_laatste_succes", "2026-07-11 00:00:00"
    )
    entry = maak_entry()
    await _setup(hass, entry)

    coordinator = entry.runtime_data
    succes = coordinator.engine_state.legionella.laatste_succes
    assert succes is not None
    assert succes.date().isoformat() == "2026-07-11"


async def test_config_flow_happy_path(hass):
    zet_states(hass)
    resultaat = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert resultaat["type"] == "form"
    invoer = {k: v for k, v in MAPPING_DEFAULTS.items() if v != ""}
    resultaat = await hass.config_entries.flow.async_configure(
        resultaat["flow_id"], user_input=invoer
    )
    assert resultaat["type"] == "create_entry"
    assert resultaat["data"]["pv_vermogen"] == MAPPING_DEFAULTS["pv_vermogen"]


async def test_config_flow_onbekende_entiteit(hass):
    zet_states(hass)
    resultaat = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    invoer = {k: v for k, v in MAPPING_DEFAULTS.items() if v != ""}
    invoer["boiler_temperatuur"] = "sensor.bestaat_niet"
    resultaat = await hass.config_entries.flow.async_configure(
        resultaat["flow_id"], user_input=invoer
    )
    assert resultaat["type"] == "form"
    assert resultaat["errors"] == {"boiler_temperatuur": "entiteit_onbekend"}


def test_store_rondreis():
    from datetime import datetime

    from custom_components.energie_manager.core.model import EngineState, Modus

    s = EngineState()
    s.actieve_modus = Modus.WARMWATER_BOOST
    s.modus_sinds = datetime(2026, 7, 12, 12, 0)
    s.warmwater_actief = True
    s.ev_ampere = 14
    s.ev_direct_laden = True
    s.legionella.laatste_succes = datetime(2026, 7, 11, 15, 0)
    s.netladen_uren_vandaag = 1.5
    s.netladen_datum = "2026-07-12"

    terug = state_uit_dict(state_naar_dict(s))
    assert terug.actieve_modus is Modus.WARMWATER_BOOST
    assert terug.warmwater_actief
    assert terug.ev_ampere == 14
    assert terug.ev_direct_laden
    assert terug.legionella.laatste_succes == s.legionella.laatste_succes
    assert terug.netladen_uren_vandaag == 1.5


def test_store_rondreis_sessie():
    from datetime import datetime

    from custom_components.energie_manager.core.model import (
        EngineState,
        SessieRecord,
        SessieState,
    )

    s = EngineState()
    s.sessie = SessieState(
        actief=True,
        start=datetime(2026, 7, 12, 11, 0),
        laatste_meter_kwh=5.19,
        energie_kwh=5.19,
        energie_gratis_kwh=4.0,
        energie_net_kwh=1.19,
        energie_ongeprijsd_kwh=0.1,
        kosten_eur=0.24,
    )
    s.sessie_historie = [
        SessieRecord(
            start=datetime(2026, 7, 11, 9, 0),
            einde=datetime(2026, 7, 11, 13, 0),
            energie_kwh=8.4,
            energie_gratis_kwh=8.4,
            energie_net_kwh=0.0,
            energie_ongeprijsd_kwh=0.0,
            kosten_eur=0.0,
        ),
        SessieRecord(
            start=datetime(2026, 7, 10, 18, 0),
            einde=datetime(2026, 7, 10, 21, 0),
            energie_kwh=12.0,
            energie_gratis_kwh=1.0,
            energie_net_kwh=11.0,
            energie_ongeprijsd_kwh=0.0,
            kosten_eur=2.31,
        ),
    ]

    terug = state_uit_dict(state_naar_dict(s))
    assert terug.sessie == s.sessie
    assert terug.sessie_historie == s.sessie_historie


async def test_sessie_sensoren(hass):
    zet_states(
        hass,
        **{
            MAPPING_DEFAULTS["ev_status_raw"]: "2",  # charging
            MAPPING_DEFAULTS["ev_vermogen"]: "10000",
            MAPPING_DEFAULTS["net_vermogen"]: "5000",
        },
    )
    entry = maak_entry()  # master off: decide only, no service calls
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    # first tick was baseline (meter 0.00); now the meter climbs 0.5 kWh
    hass.states.async_set(MAPPING_DEFAULTS["ev_sessie_energie"], "0.50")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    kosten = hass.states.get("sensor.energie_manager_ev_sessie_kosten")
    assert kosten is not None
    # half from grid (5 of 10 kW) at €0.20 -> 0.25 kWh x 0.20 = €0.05
    assert float(kosten.state) == pytest.approx(0.05)
    assert kosten.attributes["actief"] is True
    assert kosten.attributes["energie_kwh"] == pytest.approx(0.5)
    assert kosten.attributes["energie_net_kwh"] == pytest.approx(0.25)
    assert kosten.attributes["pct_gratis"] == pytest.approx(50.0)

    # charger reports charged: session finalizes into the history
    hass.states.async_set(MAPPING_DEFAULTS["ev_status_raw"], "3")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    sessies = hass.states.get("sensor.energie_manager_ev_sessies")
    assert sessies is not None
    assert sessies.attributes["aantal"] == 1
    record = sessies.attributes["sessies"][0]
    assert record["energie_kwh"] == pytest.approx(0.5)
    assert record["kosten_eur"] == pytest.approx(0.05)
    # kosten sensor now shows the completed session
    kosten = hass.states.get("sensor.energie_manager_ev_sessie_kosten")
    assert kosten.attributes["actief"] is False
    assert kosten.attributes["einde"] is not None


async def test_migratie_entry_v1_backfill(hass):
    zet_states(hass)
    data = {
        k: v
        for k, v in MAPPING_DEFAULTS.items()
        if k not in ("ev_sessie_energie", "net_vermogen")
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Energie Manager",
        unique_id=DOMAIN,
        data=data,
        version=1,
    )
    await _setup(hass, entry)
    assert entry.version == 2
    assert entry.data["ev_sessie_energie"] == MAPPING_DEFAULTS["ev_sessie_energie"]
    assert entry.data["net_vermogen"] == MAPPING_DEFAULTS["net_vermogen"]
