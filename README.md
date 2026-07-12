# Energie Manager

Home Assistant custom integration die het energiebeheer van het huis centraal regelt:
één "brein" dat elke 30 seconden precies één energiemodus kiest en bestaande
HA-entiteiten aanstuurt (Victron ESS, SG-ready boilerrelais, EV-lader, PV-limieten).
Vervangt de losse `[Energie]`-automatiseringen.

## Wat het doet

**Prioriteitsladder** (eerste match wint, elke 30 s):

1. **Noodreserve** — accu ≤ 10%: ontlading geblokkeerd, alles uit
2. **Veilige terugval** — sensoren onbeschikbaar/verouderd: eigen belastingen los, accu beschermd
3. *(overlay)* **Negatieve prijs** — teruglevering 0 W, PV-curtailment (bij volle accu), ontlading 0; verbruik (boiler/EV) blijft juist gewenst
4. **Accu goedkoop laden** — netladen in goedkope/negatieve uren (uit standaard)
5. **Warmwater boost** — SG-ready relais bij ≥ 3 kW zonne-overschot
6. **EV zonneladen** — ampères volgen het overschot (6–32 A)
7. **Batterij beschermen** — accu ≤ 25%: ontlading geblokkeerd
8. **Zelfverbruik** — standaard

Plus een **anti-legionella-bewaking** (gezondheidskritisch): wekelijkse cyclus naar
61 °C (20 min vasthouden), gepland in het 14:00–20:00-venster met voorkeur voor
goedkope/zonnige dagen, geforceerd zodra de deadline verstrijkt.

**Prijssturing** (Zonneplan-tarief + 8-uurs forecast, alles standaard UIT, elk met
eigen schakelaar): accu netladen in goedkoopste uren, warmwater op goedkope stroom,
EV laden op vast ampèrage in goedkope uren.

Anti-pendel: minimaal 600 s tussen modus-/aan-uit-wisselingen (veiligheid en
negatieve prijs uitgezonderd; EV-ampère-updates zijn vrij). Schrijven gebeurt
alleen bij afwijking van de huidige entiteitswaarde (self-healing).

## Installatie

1. HACS → Integrations → ⋮ → *Custom repositories* → deze repo, categorie *Integration*
2. Installeer **Energie Manager**, herstart HA
3. Instellingen → Apparaten & diensten → *Integratie toevoegen* → Energie Manager
4. De entity-mapping staat vooringevuld; controleer en bevestig
5. `switch.energie_manager_automatisch_beheer` staat **uit** na installatie:
   bekijk eerst een paar cycli `sensor.energie_manager_besluit_reden`, zet daarna aan

Bij de eerste start wordt `input_datetime.legionella_laatste_succes` (indien aanwezig)
automatisch overgenomen.

## Belangrijkste entiteiten

| Entiteit | Betekenis |
|---|---|
| `sensor.energie_manager_actieve_modus` | huidige modus + reden/geschiedenis in attributen |
| `sensor.energie_manager_besluit_reden` | leesbare uitleg van het laatste besluit |
| `sensor.energie_manager_zonne_overschot` | PV − verbruik − acculaden (kW) |
| `sensor.energie_manager_legionella_laatste_succes` / `_dagen_geleden` / `volgende_legionella_run` | legionellastatus |
| `sensor.energie_manager_ev_sessie_kosten` | kosten van de lopende (of laatste) EV-laadsessie; alleen netstroom telt, zon/accu = €0 (uitsplitsing in attributen) |
| `sensor.energie_manager_ev_sessies` | laatste 10 laadsessies (start/einde, kWh, % gratis, kosten) in attributen |
| `binary_sensor.energie_manager_invoer_verouderd` | probleem: welke sensoren stil zijn |
| `switch.energie_manager_automatisch_beheer` | hoofdschakelaar (uit = rekenen, niets uitvoeren) |
| overige switches | functievlaggen per onderdeel |
| numbers (categorie config) | alle drempels (3 kW, 61 °C, SoC-grenzen, prijsplafonds, …) |

## Services

- `energie_manager.forceer_modus` — modus tijdelijk forceren (veiligheid blijft voorgaan)
- `energie_manager.start_legionella` — cyclus nu starten (avondstop 20:00 blijft gelden)
- `energie_manager.forceer_evaluatie` — direct een beslissingscyclus
- `energie_manager.zet_legionella_succes` — laatste-succes corrigeren/migreren

## Vangnetten

- Hoofdschakelaar uit / integratie verwijderen ⇒ eenmalige veilige stand:
  eigen belastingen uit, teruglevering 5000 W, PV-limieten 100%, netsetpoint 50 W
- Sensoruitval ⇒ veilige terugval; prijssensor-uitval schakelt alleen prijsfuncties uit
- Mislukte commando's worden elke 30 s opnieuw geprobeerd; na 3 opeenvolgende
  fouten verschijnt een Repair-melding
- Aanbevolen: stel op de Shelly zelf een auto-uit-timer (~3 h) in als hardware-vangnet

## Ontwikkeling

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest          # 76 tests: pure decision core + integratielaag
.venv/bin/ruff check custom_components tests
```

De beslislogica staat in `custom_components/energie_manager/core/` — pure Python
zonder HA-imports, volledig unit-getest (drempels, hysterese, dwell, legionella-hold,
prijsvensters). De HA-laag (coordinator/executor) verzamelt sensorwaarden, voert de
gewenste eindtoestand idempotent uit en logt elk besluit in het logboek.
