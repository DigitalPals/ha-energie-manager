from custom_components.energie_manager.core.ev import (
    beschikbaar_kw,
    decodeer_status,
    zon_ampere,
)
from custom_components.energie_manager.core.model import Config

CONFIG = Config()


def test_decodeer_status():
    assert decodeer_status(0) == "disconnected"
    assert decodeer_status(2) == "charging"
    assert decodeer_status(4) == "waiting_for_sun"
    assert decodeer_status(99) == "unknown"
    assert decodeer_status(None) is None


def test_zon_ampere_grenzen():
    # 6 A needs 4.14 kW at 690 W/A
    assert zon_ampere(4.14, 0.0, CONFIG) == 6
    assert zon_ampere(4.13, 0.0, CONFIG) == 0  # 5 A -> below minimum
    # EV's own draw counts as available
    assert zon_ampere(0.0, 4.14, CONFIG) == 6
    # cap at 32 A (22.08 kW)
    assert zon_ampere(30.0, 0.0, CONFIG) == 32
    # exact amp math: 10 kW -> floor(14.49) = 14
    assert zon_ampere(10.0, 0.0, CONFIG) == 14


def test_beschikbaar_kw():
    assert beschikbaar_kw(1.5, 2.0) == 3.5
