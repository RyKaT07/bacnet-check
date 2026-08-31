#!/usr/bin/env python3
"""bacnet-check - uniwersalny webowy podglad urzadzen BACnet z profilami regul.

Aplikacja jest ogolna: laczy sie z dowolnym urzadzeniem BACnet/IP, pokazuje
punkty na zywo i pozwala zapisywac wartosci. Cala wiedza "co sprawdzac" siedzi
w profilach (profiles/*.json): mapowanie nazw punktow na krotkie aliasy +
reguly JS liczace "oczekiwane vs odczytane". VAV to tylko jeden z profili.

Tryby:
  python3 bacnet_check.py --sim                # symulowane urzadzenie VAV, bez sprzetu
  python3 bacnet_check.py [--ip 192.168.1.10/24]   # realny BACnet; urzadzenie wybierasz w UI

W trybie realnym wymaga:  pip install BAC0
UI: http://localhost:8342
"""
import argparse
import json
import math
import pathlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = pathlib.Path(__file__).resolve().parent
PROF_DIR = BASE / 'profiles'

VALUES = {}    # raw_name -> {'value','unit','writable'}
LOCK = threading.Lock()
STATE = {'mode': 'idle', 'device': None, 'error': None}
BACNET = None
DEVICE = None
GENERATION = 0
ARGS = None


# ── symulowane urzadzenie VAV (demo/testy regul bez sprzetu) ─────────────
SIM_PARAMS = dict(co2_sp=1000.0, propband=200.0, diff=50.0, qmin=200.0,
                  qmax=800.0, duct_h=400.0, duct_v=300.0)

def sim_loop():
    STATE.update(mode='sim', device='symulator VAV')
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
            'co2': (co2, 'ppm', False), 'co2_sp': (p['co2_sp'], 'ppm', True),
            'propband': (p['propband'], 'ppm', True), 'diff': (p['diff'], 'm3/h', True),
            'qmin': (p['qmin'], 'm3/h', True), 'qmax': (p['qmax'], 'm3/h', True),
            'duct_h': (p['duct_h'], 'mm', True), 'duct_v': (p['duct_v'], 'mm', True),
            'q_sp_setpoint': (q_sp, 'm3/h', False), 'q_ext_setpoint': (q_ext, 'm3/h', False),
            'v_sup1': (v_sup * n(), 'm/s', False), 'v_sup2': (v_sup * (2 - n()), 'm/s', False),
            'v_ext1': (v_ext * n(), 'm/s', False), 'v_ext2': (v_ext * (2 - n()), 'm/s', False),
            'damper_sup': (10 + 80 * ramp, '%', False), 'damper_ext': (10 + 78 * ramp, '%', False),
        }
        with LOCK:
            VALUES.clear()
            for k, (v, u, w) in pts.items():
                VALUES[k] = {'value': round(v, 2), 'unit': u, 'writable': w}
        time.sleep(1)


def sim_write(name, value):
    if name not in SIM_PARAMS:
        raise ValueError(f'{name} nie jest zapisywalny w symulacji')
    SIM_PARAMS[name] = float(value)


# ── realny BACnet (BAC0) ─────────────────────────────────────────────────
def bacnet_init(ip):
    global BACNET
    try:
        import BAC0
    except ImportError:
        raise SystemExit('Brak biblioteki BAC0. Zainstaluj: pip install BAC0'
                         '   (albo uruchom z --sim, bez sprzetu)')
    BACNET = BAC0.lite(ip=ip) if ip else BAC0.lite()
    STATE.update(mode='bacnet', device=None)


def bacnet_discover():
    try:
        BACNET.discover()
    except Exception:
        pass
    out = []
    for d in (getattr(BACNET, 'devices', None) or []):
        try:  # BAC0 zwraca krotki (nazwa, vendor, adres, device id)
            out.append({'name': str(d[0]), 'vendor': str(d[1]),
                        'addr': str(d[2]), 'devid': int(d[3])})
        except Exception:
            pass
    return out


def bacnet_connect(addr, devid):
    # Kazde polaczenie dostaje numer; starszy watek konczy sie sam, inaczej dwa
    # urzadzenia nadpisywalyby sobie nawzajem VALUES.
    global DEVICE, GENERATION
    import BAC0
    dev = BAC0.device(addr, int(devid), BACNET, poll=2)
    GENERATION += 1
    mine = GENERATION
    DEVICE = dev
    STATE.update(device=f'{addr} / {devid}', error=None)

    def poll():
        while GENERATION == mine:
            snap = {}
            for pt in dev.points:
                try:
                    v = pt.lastValue
                    snap[pt.properties.name] = {
                        'value': v if isinstance(v, (int, float)) else str(v),
                        'unit': str(getattr(pt.properties, 'units_state', '') or ''),
                        'writable': ('Output' in str(pt.properties.type)
                                     or 'Value' in str(pt.properties.type)),
                    }
                except Exception:
                    pass
            with LOCK:
                VALUES.clear()
                VALUES.update(snap)
            time.sleep(2)
    threading.Thread(target=poll, daemon=True).start()


def bacnet_write(name, value):
    if DEVICE is None:
        raise RuntimeError('brak polaczenia z urzadzeniem - najpierw Polacz')
    DEVICE[name] = float(value)


# ── profile (pliki JSON po stronie serwera) ──────────────────────────────
def list_profiles():
    out = {}
    for f in sorted(PROF_DIR.glob('*.json')):
        try:
            out[f.stem] = json.loads(f.read_text())
        except Exception as e:
            out[f.stem] = {'error': str(e)}
    return out


def save_profile(name, data):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name):
        raise ValueError('nazwa profilu: litery/cyfry/-/_')
    PROF_DIR.mkdir(exist_ok=True)
    keep = {k: data.get(k) for k in ('description', 'mapping', 'rules')}
    (PROF_DIR / f'{name}.json').write_text(json.dumps(keep, ensure_ascii=False, indent=1))


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

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/api/points':
            with LOCK:
                return self._json(VALUES)
        if p == '/api/state':
            return self._json(STATE)
        if p == '/api/profiles':
            return self._json(list_profiles())
        if p == '/api/discover':
            if ARGS.sim:
                return self._json([])
            try:
                return self._json(bacnet_discover())
            except Exception as e:
                return self._json({'error': str(e)}, 500)
        b = PAGE.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        p = self.path.split('?')[0]
        try:
            if p == '/api/write':
                body = self._body()
                (sim_write if ARGS.sim else bacnet_write)(body['name'], body['value'])
                return self._json({'ok': True})
            if p == '/api/connect':
                if ARGS.sim:
                    return self._json({'ok': False, 'error': 'tryb symulacji'}, 400)
                body = self._body()
                bacnet_connect(body['addr'], body['devid'])
                return self._json({'ok': True})
            if p == '/api/profiles':
                body = self._body()
                save_profile(body['name'], body)
                return self._json({'ok': True})
        except Exception as e:
            return self._json({'ok': False, 'error': str(e)}, 500)
        self._json({'error': 'not found'}, 404)


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>bacnet-check</title><style>
:root{--bg:#0f1218;--card:#1a1f2b;--border:#2a3245;--txt:#dfe4ee;--muted:#8b95a5;--ok:#3fb27f;--bad:#e05555}
body{margin:auto;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui;padding:1rem;max-width:1150px}
h1{font-size:1.1rem}h2{font-size:.85rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:.2rem 0 .5rem}
table{border-collapse:collapse;width:100%}td,th{padding:.28rem .55rem;border-bottom:1px solid var(--border);text-align:left}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.8rem;margin:.8rem 0}
input,select{background:#0d1117;color:var(--txt);border:1px solid var(--border);border-radius:6px;padding:.2rem .45rem}
input[type=number]{width:90px}
textarea{width:100%;background:#0d1117;color:#c9d4e6;border:1px solid var(--border);border-radius:8px;
 font:12px/1.5 ui-monospace,monospace;padding:.6rem;box-sizing:border-box}
button{background:#1e4da6;color:#fff;border:0;border-radius:8px;padding:.3rem .8rem;cursor:pointer}
button.sec{background:#3a4252}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}@media(max-width:850px){.grid{grid-template-columns:1fr}}
.muted{color:var(--muted)}
</style></head><body>
<h1>bacnet-check <span class="muted" style="font-weight:400">· punkty na zywo + profile regul</span></h1>
<div class="card" id="devbar">
 <span id="stateLbl" class="muted"></span>
 <span id="connectUi" style="display:none">
  <button class="sec" onclick="discover()">Szukaj urzadzen</button>
  <select id="devSel" style="min-width:180px"></select>
  adres <input id="devAddr" size="14" placeholder="192.168.1.50">
  device id <input id="devId" size="7" placeholder="1001">
  <button onclick="connectDev()">Polacz</button>
 </span>
</div>
<div class="grid">
<div class="card"><h2>Punkty (na zywo)</h2><table id="tbl"></table></div>
<div>
<div class="card"><h2>Profil regul</h2>
 <select id="profSel" onchange="pickProfile()"></select>
 <input id="profName" size="12" placeholder="nazwa">
 <button onclick="saveProfile()">Zapisz profil</button>
 <div style="margin:.5rem 0 .2rem" class="muted">mapowanie: nazwa punktu BACnet -> alias w regulach (JSON)</div>
 <textarea id="mapping" style="min-height:70px">{}</textarea>
 <div style="margin:.5rem 0 .2rem" class="muted">reguly: JS, dostaje p (aliasy), zwraca [opis, oczekiwane, odczytane, czyOK]</div>
 <textarea id="rules" style="min-height:230px"></textarea>
 <div style="margin-top:.3rem"><span id="rerr" class="bad"></span></div></div>
<div class="card"><h2>Wynik regul</h2><table id="res"></table></div>
</div></div>
<script>
const $=id=>document.getElementById(id);
let PROFILES={},editing=null;
async function loadProfiles(keep){
 PROFILES=await (await fetch('/api/profiles')).json();
 const cur=keep||localStorage.bcProfile||Object.keys(PROFILES)[0]||'';
 $('profSel').innerHTML=Object.keys(PROFILES).map(n=>`<option ${n===cur?'selected':''}>${n}</option>`).join('');
 if(cur&&PROFILES[cur])applyProfile(cur)}
function applyProfile(n){const p=PROFILES[n];localStorage.bcProfile=n;$('profName').value=n;
 $('mapping').value=JSON.stringify(p.mapping||{},null,1);$('rules').value=p.rules||''}
function pickProfile(){applyProfile($('profSel').value)}
async function saveProfile(){
 let mapping;try{mapping=JSON.parse($('mapping').value||'{}')}catch(e){alert('mapowanie: '+e.message);return}
 const name=$('profName').value.trim();
 const r=await fetch('/api/profiles',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name,mapping,rules:$('rules').value,description:''})});
 const d=await r.json();if(!d.ok)alert(d.error||'blad');else loadProfiles(name)}
async function discover(){
 const l=await (await fetch('/api/discover')).json();
 if(l.error){alert(l.error);return}
 $('devSel').innerHTML='<option value="">- znalezione -</option>'+
  l.map(d=>`<option value="${d.addr}|${d.devid}">${d.name} @ ${d.addr} (#${d.devid})</option>`).join('');
 $('devSel').onchange=()=>{const[a,i]=$('devSel').value.split('|');if(a){$('devAddr').value=a;$('devId').value=i}}}
async function connectDev(){
 const r=await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({addr:$('devAddr').value.trim(),devid:$('devId').value.trim()})});
 const d=await r.json();if(!d.ok)alert(d.error||'blad polaczenia')}
let KEYS=[];
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function writePoint(i,el){
 const r=await fetch('/api/write',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({name:KEYS[i],value:parseFloat(el.value)})});
 const d=await r.json();if(!d.ok)alert('blad zapisu: '+(d.error||''))}
async function tick(){
 try{
  const st=await (await fetch('/api/state')).json();
  $('stateLbl').textContent=st.mode==='sim'?'SYMULACJA: '+st.device
   :(st.device?'polaczono: '+st.device:'brak polaczenia');
  $('connectUi').style.display=st.mode==='sim'?'none':'inline';
  const pts=await (await fetch('/api/points')).json();
  KEYS=Object.keys(pts);
  const rows=Object.entries(pts).map(([k,v],i)=>{
   const inp=v.writable?`<input type="number" step="any" value="${typeof v.value==='number'?v.value:''}"
     onfocus="editing=${i}" onblur="editing=null" onchange="writePoint(${i},this)">`:'';
   return `<tr><td>${esc(k)}</td><td style="text-align:right">${typeof v.value==='number'?v.value.toFixed(1):esc(v.value)}</td>
    <td>${esc(v.unit||'')}</td><td>${inp}</td></tr>`});
  if(editing===null)$('tbl').innerHTML='<tr><th>punkt</th><th>wartosc</th><th>jedn.</th><th>zapis</th></tr>'+rows.join('');
  let mapping={};try{mapping=JSON.parse($('mapping').value||'{}')}catch(e){}
  const p={};for(const[k,v]of Object.entries(pts))p[mapping[k]||k]=v.value;
  let out=[];
  try{out=new Function('p',$('rules').value)(p)||[];$('rerr').textContent=''}
  catch(e){$('rerr').textContent='blad regul: '+e.message}
  $('res').innerHTML=out.length?'<tr><th>regula</th><th>oczekiwane</th><th>odczytane</th><th></th></tr>'+
   out.map(r=>`<tr><td>${r[0]}</td><td style="text-align:right">${(+r[1]).toFixed(1)}</td>
    <td style="text-align:right">${(+r[2]).toFixed(1)}</td>
    <td class="${r[3]?'ok':'bad'}">${r[3]?'OK':'ROZJAZD'}</td></tr>`).join('')
   :'<tr><td class="muted">profil bez regul albo brak punktow</td></tr>';
 }catch(e){}
 setTimeout(tick,1500)}
loadProfiles().then(tick);
</script></body></html>"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', action='store_true', help='symulowane urzadzenie VAV zamiast realnego BACnet')
    ap.add_argument('--ip', help='lokalny interfejs BACnet, np. 192.168.1.10/24')
    ap.add_argument('--port', type=int, default=8342)
    ARGS = ap.parse_args()
    if ARGS.sim:
        threading.Thread(target=sim_loop, daemon=True).start()
    else:
        bacnet_init(ARGS.ip)
    print(f'bacnet-check: http://localhost:{ARGS.port}  ({"SYMULACJA" if ARGS.sim else "BACnet"})')
    ThreadingHTTPServer(('0.0.0.0', ARGS.port), H).serve_forever()
