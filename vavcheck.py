#!/usr/bin/env python3
"""VAV check - webowy podglad sterownika VAV po BACnet z regulami wyliczajacymi.

Tryby:
  python3 vavcheck.py --sim                 # symulacja logiki z opisu programu (bez sprzetu)
  python3 vavcheck.py --addr 192.168.1.50 --devid 1001 [--ip 192.168.1.10/24]

W trybie realnym wymaga:  pip install BAC0
UI: http://localhost:8342  - tabela punktow na zywo, zapis parametrow,
edytowalne reguly JS (localStorage) porownujace "oczekiwane vs odczytane".
"""
import argparse
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Aliasowanie nazw punktow ze sterownika na krotkie nazwy uzywane w regulach.
# Po pierwszym skanie realnego sterownika wpisz tu wlasciwe nazwy BACnet.
MAPPING = {
    # 'nazwa-w-sterowniku': 'alias-w-regulach', np.:
    # 'SupAirFlowSp': 'q_sp_setpoint',
}

VALUES = {}          # name -> {'value': float, 'unit': str, 'writable': bool}
LOCK = threading.Lock()
DEVICE = None        # BAC0 device w trybie realnym


# ── symulacja wg opisu programu (CO2 wiodacy, rampa liniowa, DIFF) ────────
SIM_PARAMS = dict(co2_sp=1000.0, propband=200.0, diff=50.0, qmin=200.0,
                  qmax=800.0, duct_h=400.0, duct_v=300.0)

def sim_loop():
    t0 = time.time()
    while True:
        t = time.time() - t0
        p = SIM_PARAMS
        co2 = 900 + 300 * math.sin(t / 30)
        ramp = max(0.0, min(1.0, (co2 - (p['co2_sp'] - p['propband'])) / p['propband']))
        q_sp = p['qmin'] + ramp * (p['qmax'] - p['qmin'])
        q_ext = q_sp - p['diff']
        area = p['duct_h'] / 1000 * p['duct_v'] / 1000
        v_sup = q_sp / 3600 / area if area else 0
        v_ext = q_ext / 3600 / area if area else 0
        n = lambda: 1 + 0.02 * math.sin(t * 7)   # ponytail: szum deterministyczny wystarczy
        pts = {
            'co2': (co2, 'ppm', False),
            'co2_sp': (p['co2_sp'], 'ppm', True),
            'propband': (p['propband'], 'ppm', True),
            'diff': (p['diff'], 'm3/h', True),
            'qmin': (p['qmin'], 'm3/h', True),
            'qmax': (p['qmax'], 'm3/h', True),
            'duct_h': (p['duct_h'], 'mm', True),
            'duct_v': (p['duct_v'], 'mm', True),
            'q_sp_setpoint': (q_sp, 'm3/h', False),
            'q_ext_setpoint': (q_ext, 'm3/h', False),
            'v_sup1': (v_sup * n(), 'm/s', False),
            'v_sup2': (v_sup * (2 - n()), 'm/s', False),
            'v_ext1': (v_ext * n(), 'm/s', False),
            'v_ext2': (v_ext * (2 - n()), 'm/s', False),
            'damper_sup': (10 + 80 * ramp, '%', False),
            'damper_ext': (10 + 78 * ramp, '%', False),
        }
        with LOCK:
            VALUES.clear()
            for k, (v, u, w) in pts.items():
                VALUES[k] = {'value': round(v, 2), 'unit': u, 'writable': w}
        time.sleep(1)


def sim_write(name, value):
    if name in SIM_PARAMS:
        SIM_PARAMS[name] = float(value)
        return True
    return False


# ── tryb realny: BAC0 ─────────────────────────────────────────────────────
def bacnet_start(ip, addr, devid):
    global DEVICE
    import BAC0
    bacnet = BAC0.lite(ip=ip) if ip else BAC0.lite()
    DEVICE = BAC0.device(addr, devid, bacnet, poll=2)

    def poll():
        while True:
            snap = {}
            for pt in DEVICE.points:
                try:
                    name = pt.properties.name
                    alias = MAPPING.get(name, name)
                    unit = str(getattr(pt.properties, 'units_state', '') or '')
                    writable = 'output' in str(pt.properties.type) or 'value' in str(pt.properties.type)
                    v = pt.lastValue
                    snap[alias] = {'value': v if isinstance(v, (int, float)) else str(v),
                                   'unit': unit, 'writable': writable, 'bacnet': name}
                except Exception:
                    pass
            with LOCK:
                VALUES.clear()
                VALUES.update(snap)
            time.sleep(2)
    threading.Thread(target=poll, daemon=True).start()


def bacnet_write(name, value):
    rev = {v: k for k, v in MAPPING.items()}
    DEVICE[rev.get(name, name)] = float(value)
    return True


# ── HTTP ──────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith('/api/points'):
            with LOCK:
                return self._json(VALUES)
        b = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.path.startswith('/api/write'):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            try:
                ok = (sim_write if ARGS.sim else bacnet_write)(body['name'], body['value'])
                return self._json({'ok': bool(ok)})
            except Exception as e:
                return self._json({'ok': False, 'error': str(e)}, 500)
        self._json({'error': 'not found'}, 404)


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VAV check</title><style>
:root{--bg:#0f1218;--card:#1a1f2b;--border:#2a3245;--txt:#dfe4ee;--muted:#8b95a5;--ok:#3fb27f;--bad:#e05555}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui;padding:1rem;max-width:1100px;margin:auto}
h1{font-size:1.1rem}h2{font-size:.9rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
table{border-collapse:collapse;width:100%}td,th{padding:.3rem .6rem;border-bottom:1px solid var(--border);text-align:left}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.8rem;margin:.8rem 0}
input[type=number]{width:90px;background:#0d1117;color:var(--txt);border:1px solid var(--border);border-radius:6px;padding:.15rem .4rem}
textarea{width:100%;min-height:260px;background:#0d1117;color:#c9d4e6;border:1px solid var(--border);border-radius:8px;
 font:12px/1.5 ui-monospace,monospace;padding:.6rem;box-sizing:border-box}
button{background:#1e4da6;color:#fff;border:0;border-radius:8px;padding:.3rem .8rem;cursor:pointer}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style></head><body>
<h1>VAV check <span style="color:var(--muted);font-weight:400">· podglad + reguly wyliczajace</span></h1>
<div class="grid">
<div class="card"><h2>Punkty (na zywo)</h2><table id="tbl"></table></div>
<div>
<div class="card"><h2>Reguly (JS, edytuj i Zapisz; masz obiekt p z punktami)</h2>
<textarea id="rules"></textarea>
<div style="margin-top:.4rem"><button onclick="saveRules()">Zapisz reguly</button>
<button onclick="resetRules()" style="background:#3a4252">Domyslne</button>
<span id="rerr" class="bad"></span></div></div>
<div class="card"><h2>Wynik regul</h2><table id="res"></table></div>
</div></div>
<script>
const DEFAULT_RULES=`// p = punkty (p.nazwa). Zwroc liste wierszy:
// [opis, oczekiwane, odczytane, czyOK]
const blisko=(a,b,proc)=>Math.abs(a-b)<=Math.abs(a)*proc/100+1;
const A=p.duct_h/1000*p.duct_v/1000;                 // m2
const q_sup=(p.v_sup1+p.v_sup2)/2*A*3600;            // przeplyw nawiewu z predkosci (srednia!)
const q_ext=(p.v_ext1+p.v_ext2)/2*A*3600;
const ramp=Math.min(1,Math.max(0,(p.co2-(p.co2_sp-p.propband))/p.propband));
const q_sp_exp=p.qmin+ramp*(p.qmax-p.qmin);          // rampa CO2 wg opisu programu
const q_ext_exp=q_sp_exp-p.diff;                     // wyciag nadaza z DIFF
return [
 ['nastawa nawiewu wg rampy CO2 [m3/h]', q_sp_exp, p.q_sp_setpoint, blisko(q_sp_exp,p.q_sp_setpoint,5)],
 ['nastawa wyciagu = nawiew-DIFF [m3/h]', q_ext_exp, p.q_ext_setpoint, blisko(q_ext_exp,p.q_ext_setpoint,5)],
 ['przeplyw nawiewu z predkosci [m3/h]', q_sup, p.q_sp_setpoint, blisko(q_sup,p.q_sp_setpoint,10)],
 ['przeplyw wyciagu z predkosci [m3/h]', q_ext, p.q_ext_setpoint, blisko(q_ext,p.q_ext_setpoint,10)],
];`;
const $=id=>document.getElementById(id);
$('rules').value=localStorage.vavRules||DEFAULT_RULES;
function saveRules(){localStorage.vavRules=$('rules').value;$('rerr').textContent=''}
function resetRules(){$('rules').value=DEFAULT_RULES;saveRules()}
async function writePoint(name,el){
 const r=await fetch('/api/write',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name,value:parseFloat(el.value)})});
 const d=await r.json(); if(!d.ok) alert('blad zapisu: '+(d.error||''));}
let editing=null;
async function tick(){
 try{
  const pts=await (await fetch('/api/points')).json();
  const rows=Object.entries(pts).map(([k,v])=>{
   const val=typeof v.value==='number'?v.value:'"'+v.value+'"';
   const inp=v.writable?`<input type="number" step="any" value="${typeof v.value==='number'?v.value:''}"
     onfocus="editing='${k}'" onblur="editing=null"
     onchange="writePoint('${k}',this)">`:'';
   return `<tr><td>${k}${v.bacnet&&v.bacnet!==k?` <span style="color:var(--muted)">(${v.bacnet})</span>`:''}</td>
    <td style="text-align:right">${typeof v.value==='number'?v.value.toFixed(1):v.value}</td>
    <td>${v.unit||''}</td><td>${inp}</td></tr>`});
  if(!editing)$('tbl').innerHTML='<tr><th>punkt</th><th>wartosc</th><th>jedn.</th><th>zapis</th></tr>'+rows.join('');
  const p={};for(const[k,v]of Object.entries(pts))p[k]=v.value;
  let out=[];
  try{out=new Function('p',$('rules').value)(p)||[];$('rerr').textContent=''}
  catch(e){$('rerr').textContent='blad regul: '+e.message}
  $('res').innerHTML='<tr><th>regula</th><th>oczekiwane</th><th>odczytane</th><th></th></tr>'+
   out.map(r=>`<tr><td>${r[0]}</td><td style="text-align:right">${(+r[1]).toFixed(1)}</td>
    <td style="text-align:right">${(+r[2]).toFixed(1)}</td>
    <td class="${r[3]?'ok':'bad'}">${r[3]?'OK':'ROZJAZD'}</td></tr>`).join('');
 }catch(e){}
 setTimeout(tick,1500)}
tick();
</script></body></html>"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', action='store_true', help='symulacja bez sprzetu')
    ap.add_argument('--addr', help='adres IP sterownika BACnet')
    ap.add_argument('--devid', type=int, help='device instance sterownika')
    ap.add_argument('--ip', help='lokalny interfejs, np. 192.168.1.10/24')
    ap.add_argument('--port', type=int, default=8342)
    ARGS = ap.parse_args()
    if ARGS.sim:
        threading.Thread(target=sim_loop, daemon=True).start()
    else:
        if not (ARGS.addr and ARGS.devid):
            ap.error('podaj --addr i --devid albo uzyj --sim')
        bacnet_start(ARGS.ip, ARGS.addr, ARGS.devid)
    print(f'VAV check: http://localhost:{ARGS.port}  ({"SYMULACJA" if ARGS.sim else "BACnet " + ARGS.addr})')
    ThreadingHTTPServer(('0.0.0.0', ARGS.port), H).serve_forever()
