"""Executes the engine's desired state against real HA entities.

Only-on-change: each command is compared to the target entity's current
state and skipped when already satisfied. Failures are retried implicitly
by the next 30 s tick (the engine re-emits the full desired state); after
repeated consecutive failures of the same target a repair issue is raised.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .const import CONF_DOEL, DOMAIN
from .core.model import Commando, Doel

_LOGGER = logging.getLogger(__name__)

# numeric compare tolerance per target
_TOLERANTIE: dict[Doel, float] = {
    Doel.EV_STROOM: 0.5,
    Doel.FEED_IN: 50.0,
    Doel.MAX_ONTLADING: 50.0,
    Doel.NET_SETPOINT: 100.0,
    Doel.SOLAR_LIMIET_1: 0.5,
    Doel.SOLAR_LIMIET_2: 0.5,
    Doel.KOEL_OFFSET: 0.4,  # integer steps on the Nibe number
}

_FOUT_DREMPEL_REPAIR = 3


class Uitvoerder:
    def __init__(self, hass: HomeAssistant, mapping: dict[str, str]) -> None:
        self._hass = hass
        self._mapping = mapping  # conf-key -> entity_id
        self._fouten: dict[Doel, int] = {}
        self._buiten_bereik_gemeld: set[Doel] = set()

    def entity_id(self, doel: Doel) -> str | None:
        return self._mapping.get(CONF_DOEL[doel]) or None

    @property
    def fouten(self) -> dict[str, int]:
        return {str(doel): n for doel, n in self._fouten.items() if n}

    def _buiten_bereik(self, entity_id: str, waarde: float) -> tuple | None:
        """Return (min, max) when the value falls outside the target's range.

        Out-of-range writes are SKIPPED, not clamped: during startup the
        template numbers briefly report a default 0-100 range, and a clamped
        write would then push a wrong value (e.g. max_ontlading 100 W) onto
        the ESS. Skipping keeps the previous value; the next tick retries
        once the entity reports its real range. A permanently mis-ranged
        entity (e.g. a setpoint that does not allow negatives) degrades
        visibly via the warning instead of a 3-strike repair issue."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        ondergrens = state.attributes.get("min")
        bovengrens = state.attributes.get("max")
        if isinstance(ondergrens, (int, float)) and waarde < ondergrens:
            return (ondergrens, bovengrens)
        if isinstance(bovengrens, (int, float)) and waarde > bovengrens:
            return (ondergrens, bovengrens)
        return None

    def _is_al_zo(self, doel: Doel, entity_id: str, waarde: float | bool) -> bool:
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return False  # try anyway; failure surfaces via the counter
        if isinstance(waarde, bool):
            return (state.state == "on") is waarde
        try:
            huidig = float(state.state)
        except ValueError:
            return False
        return abs(huidig - float(waarde)) <= _TOLERANTIE.get(doel, 0.01)

    async def voer_uit(self, commandos: tuple[Commando, ...]) -> None:
        for cmd in commandos:
            entity_id = self.entity_id(cmd.doel)
            if not entity_id:
                continue
            waarde = cmd.waarde
            if not isinstance(waarde, bool):
                bereik = self._buiten_bereik(entity_id, float(waarde))
                if bereik is not None:
                    if cmd.doel not in self._buiten_bereik_gemeld:
                        self._buiten_bereik_gemeld.add(cmd.doel)
                        _LOGGER.warning(
                            "%s: %s buiten bereik [%s, %s] van %s — schrijven "
                            "overgeslagen tot het bereik klopt",
                            cmd.doel,
                            waarde,
                            bereik[0],
                            bereik[1],
                            entity_id,
                        )
                    continue
                self._buiten_bereik_gemeld.discard(cmd.doel)
            if self._is_al_zo(cmd.doel, entity_id, waarde):
                self._fouten.pop(cmd.doel, None)
                continue
            try:
                if isinstance(waarde, bool):
                    await self._hass.services.async_call(
                        "switch",
                        "turn_on" if waarde else "turn_off",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                else:
                    await self._hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": entity_id, "value": waarde},
                        blocking=True,
                    )
                self._fouten.pop(cmd.doel, None)
                _LOGGER.debug("%s -> %s (%s)", entity_id, waarde, cmd.reden)
            except HomeAssistantError as err:
                aantal = self._fouten.get(cmd.doel, 0) + 1
                self._fouten[cmd.doel] = aantal
                _LOGGER.warning(
                    "Command %s -> %s failed (%d consecutive): %s",
                    entity_id,
                    cmd.waarde,
                    aantal,
                    err,
                )
                if aantal == _FOUT_DREMPEL_REPAIR:
                    ir.async_create_issue(
                        self._hass,
                        DOMAIN,
                        f"commando_fout_{cmd.doel}",
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key="commando_fout",
                        translation_placeholders={
                            "entity_id": entity_id,
                            "waarde": str(cmd.waarde),
                        },
                    )

    async def veilige_stand(
        self,
        *,
        relais_uitzetten: bool,
        ev_uitzetten: bool,
        feed_in_w: float,
        ontlading_w: float,
        setpoint_w: float,
        koel_offset_herstellen: bool = False,
        koel_offset: float = 0.0,
    ) -> None:
        """One-time safe-state release (unload / master switch off)."""
        cmds: list[Commando] = []
        if relais_uitzetten:
            cmds.append(Commando(Doel.WARMWATER_RELAIS, False, "veilige stand"))
        if ev_uitzetten:
            cmds.append(Commando(Doel.EV_SCHAKELAAR, False, "veilige stand"))
        if koel_offset_herstellen:
            cmds.append(Commando(Doel.KOEL_OFFSET, koel_offset, "veilige stand"))
        cmds += [
            Commando(Doel.FEED_IN, feed_in_w, "veilige stand"),
            Commando(Doel.MAX_ONTLADING, ontlading_w, "veilige stand"),
            Commando(Doel.NET_SETPOINT, setpoint_w, "veilige stand"),
            Commando(Doel.SOLAR_LIMIET_1, 100.0, "veilige stand"),
            Commando(Doel.SOLAR_LIMIET_2, 100.0, "veilige stand"),
        ]
        await self.voer_uit(tuple(cmds))
