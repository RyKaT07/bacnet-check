"""Symulowany sterownik VAV Nefryt - layout (REGULACJA GLOWNA / NAWIEW / WYCIAG),
w wariancie z osobnymi wymiarami per kanal.

Sterownik ma 4 wejscia predkosci (UIO4-UIO7): po dwa na nawiew i na wyciag, wiec
uklady 2N1W / 1N2W / 2N2W maja po dwa kanaly na jednej stronie. Kazdy kanal ma
wlasny przekroj, dlatego przeplyw to suma v1*A1 + v2*A2, a NIE (v1+v2)*A.
Kanal nieuzywany zostawia sie z wymiarami 0 - wtedy nie wnosi nic do sumy.

Kontrakt symulatora (skopiuj ten plik, zeby zrobic wlasny):
  PARAMS  - punkty zapisywalne + wartosci poczatkowe
  UNITS   - jednostki punktow (opcjonalnie)
  step(t, p, s) -> dict punktow tylko do odczytu
"""
import math

# Domyslnie uklad 2N2W (jak szacht NW1/1), kanaly celowo roznej wielkosci.
# Wymiary w METRACH, wspolczynniki K dla sond na czerpni/pod katem (1.0 = brak korekty).
PARAMS = dict(
    Cfg_Mode=1.0, Cfg_MinFlow=50.0, Cfg_MaxFlow=10000.0,
    Cfg_CO2PropBand=400.0, Cfg_CO2Setpoint=1500.0, Cfg_Diff=0.0,
    Cfg_Supply1H=0.75, Cfg_Supply1V=0.15, Cfg_Supply2H=0.50, Cfg_Supply2V=0.15,
    Cfg_Return1H=0.50, Cfg_Return1V=0.30, Cfg_Return2H=0.40, Cfg_Return2V=0.25,
    Cfg_KSupply1=1.0, Cfg_KSupply2=1.0, Cfg_KReturn1=1.0, Cfg_KReturn2=1.0,
)

_M = {k: 'm' for k in PARAMS if k.endswith(('H', 'V'))}
UNITS = {'Cfg_Mode': '1-AUTO 2-MIN 3-MAX 4-OFF',
         'Cfg_MinFlow': 'm3/h', 'Cfg_MaxFlow': 'm3/h', 'Cfg_Diff': 'm3/h',
         'Cfg_CO2PropBand': 'ppm', 'Cfg_CO2Setpoint': 'ppm',
         'ActualCO2': 'ppm',
         'ActualSpeedSupply1': 'm/s', 'ActualSpeedSupply2': 'm/s',
         'ActualSpeedReturn1': 'm/s', 'ActualSpeedReturn2': 'm/s',
         'ActualFlowSupply': 'm3/h', 'ActualFlowReturn': 'm3/h',
         'ActualPositionSupply': '%', 'ActualPositionReturn': '%', **_M}


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

    a_s1 = p['Cfg_Supply1H'] * p['Cfg_Supply1V']
    a_s2 = p['Cfg_Supply2H'] * p['Cfg_Supply2V']
    a_r1 = p['Cfg_Return1H'] * p['Cfg_Return1V']
    a_r2 = p['Cfg_Return2H'] * p['Cfg_Return2V']
    q_sup, q_ext = s['q_sup'], s['q_ext']

    # Kanaly rownolegle na jednej przepustnicy: ta sama predkosc w obu,
    # wiec przeplyw dzieli sie proporcjonalnie do przekroju.
    def predkosc(q, *areas):
        suma = sum(areas)
        return (q / 3600 / suma) if suma else 0.0

    v_sup = predkosc(q_sup, a_s1, a_s2)
    v_ext = predkosc(q_ext, a_r1, a_r2)
    span = p['Cfg_MaxFlow'] or 1
    return {
        'ActualCO2': co2,
        'ActualSpeedSupply1': v_sup / (p['Cfg_KSupply1'] or 1) if a_s1 else 0.0,
        'ActualSpeedSupply2': v_sup / (p['Cfg_KSupply2'] or 1) if a_s2 else 0.0,
        'ActualSpeedReturn1': v_ext / (p['Cfg_KReturn1'] or 1) if a_r1 else 0.0,
        'ActualSpeedReturn2': v_ext / (p['Cfg_KReturn2'] or 1) if a_r2 else 0.0,
        'ActualFlowSupply': q_sup,
        'ActualFlowReturn': q_ext,
        'ActualPositionSupply': min(100.0, 100 * q_sup / span),
        'ActualPositionReturn': min(100.0, 100 * q_ext / span),
    }
