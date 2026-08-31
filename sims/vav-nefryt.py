"""Symulowany sterownik VAV Nefryt - odwzorowuje layout (REGULACJA GLOWNA / NAWIEW / WYCIAG).

Kontrakt symulatora (skopiuj ten plik, zeby zrobic wlasny):
  PARAMS  - punkty zapisywalne + wartosci poczatkowe (to, co w sterowniku jest Cfg_*)
  UNITS   - jednostki punktow (opcjonalnie)
  step(t, p, s) -> dict punktow tylko do odczytu
                   t = sekundy od startu, p = biezace PARAMS, s = wlasny stan miedzy krokami
"""
import math

# Wymiary kanalow w METRACH, tak jak w sterowniku (0,75 x 0,15).
PARAMS = dict(Cfg_Mode=1.0, Cfg_MinFlow=50.0, Cfg_MaxFlow=10000.0,
              Cfg_CO2PropBand=400.0, Cfg_CO2Setpoint=1500.0, Cfg_Diff=0.0,
              Cfg_SupplyH=0.75, Cfg_SupplyV=0.15,
              Cfg_ReturnH=0.50, Cfg_ReturnV=0.30)

UNITS = {'Cfg_Mode': '1-AUTO 2-MIN 3-MAX 4-OFF',
         'Cfg_MinFlow': 'm3/h', 'Cfg_MaxFlow': 'm3/h', 'Cfg_Diff': 'm3/h',
         'Cfg_CO2PropBand': 'ppm', 'Cfg_CO2Setpoint': 'ppm',
         'Cfg_SupplyH': 'm', 'Cfg_SupplyV': 'm', 'Cfg_ReturnH': 'm', 'Cfg_ReturnV': 'm',
         'ActualCO2': 'ppm', 'ActualSpeedSupply': 'm/s', 'ActualSpeedReturn': 'm/s',
         'ActualFlowSupply': 'm3/h', 'ActualFlowReturn': 'm3/h',
         'ActualPositionSupply': '%', 'ActualPositionReturn': '%'}


def step(t, p, s):
    co2 = 1200 + 400 * math.sin(t / 30)

    # regulacja glowna: rampa CO2 od (nastawa - PropBand) do nastawy
    band = p['Cfg_CO2PropBand'] or 1
    x1 = p['Cfg_CO2Setpoint'] - p['Cfg_CO2PropBand']
    ramp = max(0.0, min(1.0, (co2 - x1) / band))
    sp_auto = p['Cfg_MinFlow'] + ramp * (p['Cfg_MaxFlow'] - p['Cfg_MinFlow'])

    # NumericSelect: 1-AUTO, 2-MIN, 3-MAX, 4-OFF
    mode = int(p['Cfg_Mode'])
    sp_sup = {1: sp_auto, 2: p['Cfg_MinFlow'], 3: p['Cfg_MaxFlow']}.get(mode, 0.0)
    sp_ext = sp_sup - p['Cfg_Diff']

    # PID + przepustnica: nadazanie pierwszego rzedu
    for key, sp in (('q_sup', sp_sup), ('q_ext', sp_ext)):
        s[key] = s.get(key, 0.0) + (max(0.0, sp) - s.get(key, 0.0)) * 0.25

    a_sup = p['Cfg_SupplyH'] * p['Cfg_SupplyV']
    a_ext = p['Cfg_ReturnH'] * p['Cfg_ReturnV']
    q_sup, q_ext = s['q_sup'], s['q_ext']
    span = p['Cfg_MaxFlow'] or 1
    return {
        'ActualCO2': co2,
        # sterownik liczy przeplyw jako suma_predkosci * przekroj * 3600,
        # wiec raportowana predkosc to odwrotnosc tego rachunku
        'ActualSpeedSupply': q_sup / 3600 / a_sup if a_sup else 0,
        'ActualFlowSupply': q_sup,
        'ActualSpeedReturn': q_ext / 3600 / a_ext if a_ext else 0,
        'ActualFlowReturn': q_ext,
        'ActualPositionSupply': min(100.0, 100 * q_sup / span),
        'ActualPositionReturn': min(100.0, 100 * q_ext / span),
    }
