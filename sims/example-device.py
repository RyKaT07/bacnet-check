"""Example simulated device: a heating loop with one valve.

Generic on purpose - it exists to document the contract, not to model anyone's
hardware. Copy it, rename it and describe your own device.

Simulator contract:
  PARAMS  - writable points and their initial values
  UNITS   - point units (optional)
  step(t, p, s) -> dict of read-only points
                   t = seconds since start, p = current PARAMS,
                   s = your own state carried between steps
"""
import math

PARAMS = dict(Cfg_TempSetpoint=21.0, Cfg_ValveMin=0.0, Cfg_ValveMax=100.0)

UNITS = {'Cfg_TempSetpoint': 'degC', 'Cfg_ValveMin': '%', 'Cfg_ValveMax': '%',
         'ActualTemp': 'degC', 'ActualValve': '%', 'ActualOutsideTemp': 'degC'}


def step(t, p, s):
    outside = 5 + 8 * math.sin(t / 120)
    temp = s.get('temp', 18.0)

    # Proportional valve: the colder it is against the setpoint, the more it opens.
    error = p['Cfg_TempSetpoint'] - temp
    valve = min(p['Cfg_ValveMax'], max(p['Cfg_ValveMin'], 50 * error))

    # Room: heated by the valve, cooled by the outside temperature.
    s['temp'] = temp + (valve / 100 * 1.2 - (temp - outside) * 0.02) * 0.3
    return {'ActualTemp': s['temp'], 'ActualValve': valve, 'ActualOutsideTemp': outside}
