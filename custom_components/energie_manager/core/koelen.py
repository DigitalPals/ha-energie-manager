"""Pre-cool channel: bank cold in the floor mass on big solar surplus.

Actuated by writing the Nibe cooling-curve offset (a sustained demand shift;
the heat pump's own degree-minutes integrator and compressor min-run logic
provide short-cycle protection). Sits below warmwater and EV in surplus
priority: warmwater reserves its boost power explicitly and the EV's draw is
already inside ac_load_w, so the remaining surplus collapses while the car
charges.

Missing indoor/outdoor temperature only disables this channel (never a
safety fallback): reverting a cooling boost carries no health or hardware
risk, so the fail-safe is simply "offset back to normal".
"""

from __future__ import annotations

from datetime import datetime

from .model import Config, EngineState, Invoer


def gewenst(
    s: EngineState,
    invoer: Invoer,
    config: Config,
    overschot_voor_koelen: float,
    soc: float,
    nu: datetime,
) -> tuple[bool, bool, str]:
    """Desired pre-cool state: (gewenst, dwell_exempt, reden).

    Mutates only this channel's hysteresis timers on ``s``.
    """
    if not config.voorkoelen_aan:
        return False, True, "voorkoelen uit"

    binnen = invoer.binnen_temp
    buiten = invoer.buiten_temp

    # condensation guard: the coldest floor must stay above the dew point.
    # With the guard enabled (marge_min > 0) a missing margin value blocks
    # the channel entirely — wet floors are worse than a skipped pre-cool.
    dauw_bewaakt = config.dauwpunt_marge_min_c > 0
    marge = invoer.dauwpunt_marge_c

    if not s.voorkoelen_actief:
        s.voorkoelen_overschot_laag_sinds = None
        s.voorkoelen_soc_laag_sinds = None
        if binnen is None or buiten is None:
            return False, False, ""
        if dauw_bewaakt and (marge is None or marge < config.dauwpunt_marge_min_c):
            return False, False, ""
        if buiten < config.voorkoelen_buiten_min_c:
            return False, False, ""
        if binnen <= config.voorkoelen_vloer_c + 0.3:  # re-entry hysteresis
            return False, False, ""
        if soc < config.batterij_prioriteit_soc:
            return False, False, ""
        if overschot_voor_koelen < config.voorkoelen_drempel_kw:
            return False, False, ""
        return (
            True,
            False,
            f"voorkoelen: overschot {overschot_voor_koelen:.1f} kW, "
            f"binnen {binnen:.1f}°",
        )

    # channel is active: evaluate off-conditions
    if binnen is None or buiten is None:
        return False, True, "voorkoelen gestopt: temperatuur onbekend"
    if dauw_bewaakt and marge is None:
        return False, True, "voorkoelen gestopt: dauwpuntmarge onbekend"
    if dauw_bewaakt and marge < config.dauwpunt_marge_min_c - 1.0:
        return (
            False,
            True,
            f"voorkoelen gestopt: vloer {marge:.1f}° boven dauwpunt (condensrisico)",
        )
    if binnen <= config.voorkoelen_vloer_c:
        return False, True, f"voorkoelen klaar: binnen {binnen:.1f}° op comfortvloer"
    if buiten < config.voorkoelen_buiten_min_c - 1.0:
        return False, False, f"voorkoelen gestopt: buiten {buiten:.1f}°"

    if overschot_voor_koelen < config.voorkoelen_uitschakel_kw:
        if s.voorkoelen_overschot_laag_sinds is None:
            s.voorkoelen_overschot_laag_sinds = nu
        elif (
            nu - s.voorkoelen_overschot_laag_sinds
        ).total_seconds() >= config.voorkoelen_uitschakel_vertraging_s:
            return (
                False,
                False,
                f"voorkoelen gestopt: overschot {overschot_voor_koelen:.1f} kW",
            )
    else:
        s.voorkoelen_overschot_laag_sinds = None

    if soc < config.warmwater_soc_uitschakel:
        if s.voorkoelen_soc_laag_sinds is None:
            s.voorkoelen_soc_laag_sinds = nu
        elif (
            nu - s.voorkoelen_soc_laag_sinds
        ).total_seconds() >= config.warmwater_soc_vertraging_s:
            return False, False, f"voorkoelen gestopt: accu {soc:.0f}% heeft voorrang"
    else:
        s.voorkoelen_soc_laag_sinds = None

    return True, False, f"voorkoelen actief ({overschot_voor_koelen:.1f} kW overschot)"
