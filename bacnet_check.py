#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["BAC0>=2026.7.25"]
# ///
"""bacnet-check - uniwersalny webowy podglad urzadzen BACnet z profilami regul.

Aplikacja jest ogolna: laczy sie z dowolnym urzadzeniem BACnet/IP, pokazuje
punkty na zywo i pozwala zapisywac wartosci. Cala wiedza "co sprawdzac" siedzi
w profilach (profiles/*.json): mapowanie nazw punktow na krotkie aliasy +
reguly JS liczace "oczekiwane vs odczytane". VAV to tylko jeden z profili.

Tryby (uv sam ogarnia srodowisko - zaleznosci sa w naglowku powyzej):
  uv run bacnet_check.py --sim [nazwa]          # symulator z sims/<nazwa>.py, bez sprzetu
  uv run bacnet_check.py [--ip 192.168.1.10/24] # realny BACnet; urzadzenie wybierasz w UI

UI: http://localhost:8342
"""
import argparse
import importlib.util
import json
import pathlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = pathlib.Path(__file__).resolve().parent
PROF_DIR = BASE / 'profiles'
SIM_DIR = BASE / 'sims'

VALUES = {}    # raw_name -> {'value','unit','writable'}
LOCK = threading.Lock()
STATE = {'mode': 'idle', 'device': None, 'error': None}
BACNET = None
DEVICE = None
GENERATION = 0
ARGS = None


# ── symulowane urzadzenie: zwykly plik w sims/ (patrz sims/vav-nefryt.py) ─
SIM = None          # zaladowany modul symulatora
SIM_PARAMS = {}     # punkty zapisywalne symulatora


def load_sim(name):
    global SIM, SIM_PARAMS
    files = sorted(SIM_DIR.glob('*.py'))
    if not files:
        raise SystemExit(f'Brak symulatorow w {SIM_DIR}. Zrob wlasny wg sims/vav-nefryt.py')
    if name:
        path = SIM_DIR / f'{name}.py'
        if not path.is_file():
            raise SystemExit(f'Nie ma sims/{name}.py. Dostepne: '
                             + ', '.join(f.stem for f in files))
    else:
        path = files[0]
    spec = importlib.util.spec_from_file_location(path.stem, path)
    SIM = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(SIM)
    SIM_PARAMS = dict(SIM.PARAMS)
    return path.stem


def sim_loop():
    units = getattr(SIM, 'UNITS', {})
    state, t0 = {}, time.time()
    while True:
        reads = SIM.step(time.time() - t0, SIM_PARAMS, state)
        with LOCK:
            VALUES.clear()
            for k, v in SIM_PARAMS.items():
                VALUES[k] = {'value': round(v, 3), 'unit': units.get(k, ''), 'writable': True}
            for k, v in reads.items():
                VALUES[k] = {'value': round(v, 2) if isinstance(v, (int, float)) else v,
                             'unit': units.get(k, ''), 'writable': False}
        time.sleep(1)


def sim_write(name, value):
    if name not in SIM_PARAMS:
        raise ValueError(f'{name} nie jest zapisywalny w tym symulatorze')
    SIM_PARAMS[name] = float(value)


# ── realny BACnet (BAC0) ─────────────────────────────────────────────────
def bacnet_init(ip):
    global BACNET
    try:
        import BAC0
    except ImportError:
        raise SystemExit('Brak biblioteki BAC0. Uruchom przez: uv run bacnet_check.py'
                         '   (albo z --sim, bez sprzetu)')
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
            if ARGS.sim is not None:
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
                (sim_write if ARGS.sim is not None else bacnet_write)(body['name'], body['value'])
                return self._json({'ok': True})
            if p == '/api/connect':
                if ARGS.sim is not None:
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
body{margin:auto;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui;padding:1rem;max-width:1800px}
h1{font-size:1.1rem}h2{font-size:.85rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:.2rem 0 .5rem}
table{border-collapse:collapse;width:100%}td,th{padding:.28rem .55rem;border-bottom:1px solid var(--border);text-align:left}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.8rem;margin:.8rem 0}
input,select{background:#0d1117;color:var(--txt);border:1px solid var(--border);border-radius:6px;padding:.2rem .45rem}
input[type=number]{width:90px}
textarea{width:100%;background:#0d1117;color:#c9d4e6;border:1px solid var(--border);border-radius:8px;
 font:12px/1.5 ui-monospace,monospace;padding:.6rem;box-sizing:border-box}
/* Edytor regul: kolorowanie robi <pre> pod spodem, textarea jest przezroczysta.
   Obie warstwy MUSZA miec identyczna czcionke, padding i interlinie, inaczej
   kursor rozjedzie sie z tekstem. */
.ed{display:flex;background:#0d1117;border:1px solid var(--border);border-radius:8px;overflow:hidden}
.ed .gut{padding:.6rem .45rem;text-align:right;color:#4a5568;background:#0b0f14;
 font:12px/1.5 ui-monospace,monospace;white-space:pre;overflow:hidden;user-select:none}
.ed .wrap{position:relative;flex:1;min-height:clamp(300px,55vh,820px)}
.ed pre,.ed textarea{position:absolute;inset:0;margin:0;padding:.6rem;border:0;box-sizing:border-box;
 font:12px/1.5 ui-monospace,monospace;white-space:pre;tab-size:2}
.ed pre{pointer-events:none;overflow:hidden;color:#c9d4e6}
.ed textarea{background:transparent;color:transparent;caret-color:#fff;resize:none;outline:none;overflow:auto}
.ed i{font-style:normal}
.ed i.c{color:#6a7a8c;font-style:italic}
.ed i.s{color:#9ece6a}
.ed i.k{color:#7aa2f7}
.ed i.n{color:#e0af68}
.ed i.f{color:#7dcfff}
button{background:#1e4da6;color:#fff;border:0;border-radius:8px;padding:.3rem .8rem;cursor:pointer}
button.sec{background:#3a4252}
.ok{color:var(--ok);font-weight:700}.bad{color:var(--bad);font-weight:700}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media(max-width:850px){.grid{grid-template-columns:1fr}}
/* Tabela punktow ma stala szerokosc tresci - na szerokim ekranie caly nadmiar
   miejsca oddajemy edytorowi regul zamiast dzielic strone po polowie. */
@media(min-width:1250px){.grid{grid-template-columns:minmax(340px,26%) 1fr}}
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
 <button class="sec" onclick="newProfile()">Nowy</button>
 <div style="margin:.5rem 0 .2rem" class="muted">mapowanie: nazwa punktu BACnet -> alias w regulach (JSON)
  <button class="sec" style="padding:.1rem .5rem;font-size:.8em" onclick="fillMapping()">Wypelnij z punktow</button></div>
 <textarea id="mapping" style="min-height:70px">{}</textarea>
 <div style="margin:.5rem 0 .2rem" class="muted">reguly: JS, dostaje p (aliasy) i prev (poprzedni odczyt), zwraca [opis, oczekiwane, odczytane, czyOK]
  <button class="sec" style="padding:.1rem .5rem;font-size:.8em" onclick="formatRules()">Formatuj</button></div>
 <div class="ed"><div class="gut" id="gut">1</div><div class="wrap">
  <pre id="hl"></pre><textarea id="rules" spellcheck="false" oninput="syncEd()" onscroll="syncEd()"></textarea>
 </div></div>
 <div style="margin-top:.3rem"><span id="rerr" class="bad"></span></div></div>
<div class="card"><h2>Wynik regul</h2><table id="res"></table></div>
</div></div>
<script>
const $=id=>document.getElementById(id);
let PROFILES={},editing=null,PREV=null;
async function loadProfiles(keep){
 PROFILES=await (await fetch('/api/profiles')).json();
 const cur=keep||localStorage.bcProfile||Object.keys(PROFILES)[0]||'';
 $('profSel').innerHTML=Object.keys(PROFILES).map(n=>`<option ${n===cur?'selected':''}>${n}</option>`).join('');
 if(cur&&PROFILES[cur])applyProfile(cur)}
function applyProfile(n){const p=PROFILES[n];localStorage.bcProfile=n;$('profName').value=n;
 $('mapping').value=JSON.stringify(p.mapping||{},null,1);$('rules').value=p.rules||'';syncEd()}
function pickProfile(){applyProfile($('profSel').value)}
const STARTER=`// p = punkty po zmapowaniu aliasow (patrz pole mapowania powyzej).
// Zwroc liste wierszy: [opis, oczekiwane, odczytane, czyOK]
const blisko=(a,b,proc)=>Math.abs(a-b)<=Math.abs(a)*proc/100+1;
return [
 // ['co sprawdzam', p.alias_oczekiwany, p.alias_odczytany, blisko(p.alias_oczekiwany,p.alias_odczytany,5)],
];`;
function newProfile(){$('profName').value='';$('mapping').value='{}';$('rules').value=STARTER;syncEd();
 $('rerr').textContent='wpisz nazwe i Zapisz profil'}
// Wciaga nazwy punktow prosto ze sterownika - alias na start rowny nazwie,
// prawa strone poprawiasz recznie na to, czego uzywasz w regulach.
function fillMapping(){
 let cur={};try{cur=JSON.parse($('mapping').value||'{}')}catch(e){}
 const out={};KEYS.forEach(k=>out[k]=cur[k]||k);
 $('mapping').value=JSON.stringify(out,null,1)}
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
// ── edytor regul: kolorowanie, numery linii, wciecia ─────────────────────
const KW=/^(const|let|var|function|return|if|else|for|of|in|while|do|new|delete|typeof|instanceof|true|false|null|undefined|break|continue|switch|case|default|try|catch|finally|throw|class|this)$/;
// Jeden przebieg po zrodle: komentarz | string | liczba | slowo. Kolejnosc ma
// znaczenie - inaczej slowo wewnatrz stringu dostaloby kolor slowa kluczowego.
const TOK=/(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][A-Za-z0-9_$]*)/g;
function hl(src){
 let out='',last=0,m;TOK.lastIndex=0;
 while((m=TOK.exec(src))!==null){
  out+=esc(src.slice(last,m.index));
  const t=esc(m[0]);
  if(m[1])out+='<i class=c>'+t+'</i>';
  else if(m[2])out+='<i class=s>'+t+'</i>';
  else if(m[3])out+='<i class=n>'+t+'</i>';
  else if(KW.test(m[0]))out+='<i class=k>'+t+'</i>';
  else out+=(src[TOK.lastIndex]==='(')?'<i class=f>'+t+'</i>':t;
  last=TOK.lastIndex;
 }
 return out+esc(src.slice(last));
}
function syncEd(){
 const ta=$('rules');if(!ta)return;
 $('hl').innerHTML=hl(ta.value)+'\n';
 const n=ta.value.split('\n').length;
 let g='';for(let i=1;i<=n;i++)g+=i+'\n';
 $('gut').textContent=g;
 $('hl').scrollTop=$('gut').scrollTop=ta.scrollTop;$('hl').scrollLeft=ta.scrollLeft;
}
// Usuwa stringi i komentarze, zeby liczyc nawiasy tylko w kodzie.
const bezTekstu=l=>l.replace(/\/\*[\s\S]*?\*\//g,'').replace(/\/\/.*$/,'')
 .replace(/'(?:\\.|[^'\\])*'/g,"''").replace(/"(?:\\.|[^"\\])*"/g,'""').replace(/`(?:\\.|[^`\\])*`/g,'``');
// Rownanie wciec wg glebokosci nawiasow. Zmienia WYLACZNIE biale znaki na
// poczatku linii, wiec najgorsze co moze zrobic to brzydkie wciecie.
function formatRules(){
 const ta=$('rules');let d=0,wiszace=0;
 ta.value=ta.value.split('\n').map(l=>{
  const t=l.trim();if(!t)return '';
  const kod=bezTekstu(t);
  const zamykaNaStarcie=(kod.match(/^[}\])]+/)||[''])[0].length;
  const poziom=Math.max(0,d-zamykaNaStarcie)+wiszace;
  d=Math.max(0,d+(kod.match(/[{[(]/g)||[]).length-(kod.match(/[}\])]/g)||[]).length);
  // if/for/while bez klamry: cialo to nastepna linia, wciecie tylko dla niej
  wiszace=/^(if|for|while)\b[^{]*\)$|^else$/.test(kod)?1:0;
  return '  '.repeat(poziom)+t;
 }).join('\n');
 syncEd();
}
document.addEventListener('keydown',e=>{
 if(e.target.id!=='rules')return;
 const ta=e.target,s=ta.selectionStart,en=ta.selectionEnd;
 if(e.key==='Tab'){e.preventDefault();
  ta.value=ta.value.slice(0,s)+'  '+ta.value.slice(en);
  ta.selectionStart=ta.selectionEnd=s+2;syncEd()}
 else if(e.key==='Enter'){e.preventDefault();
  const linia=ta.value.slice(0,s).split('\n').pop();
  const w=(linia.match(/^\s*/)||[''])[0]+(/[{[(]\s*$/.test(bezTekstu(linia))?'  ':'');
  ta.value=ta.value.slice(0,s)+'\n'+w+ta.value.slice(en);
  ta.selectionStart=ta.selectionEnd=s+1+w.length;syncEd()}
});
const nd=r=>!isFinite(+r[1])||!isFinite(+r[2]);
const fmt=v=>!isFinite(+v)?'-':Math.abs(v)<10?(+v).toFixed(2):(+v).toFixed(1);
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
   return `<tr><td>${esc(k)}</td><td style="text-align:right">${typeof v.value==='number'?fmt(v.value):esc(v.value)}</td>
    <td>${esc(v.unit||'')}</td><td>${inp}</td></tr>`});
  if(editing===null)$('tbl').innerHTML='<tr><th>punkt</th><th>wartosc</th><th>jedn.</th><th>zapis</th></tr>'+rows.join('');
  let mapping={};try{mapping=JSON.parse($('mapping').value||'{}')}catch(e){}
  const p={};for(const[k,v]of Object.entries(pts))p[mapping[k]||k]=v.value;
  let out=[],hint='';
  const src=$('rules').value;
  try{
   out=new Function('p','prev',src)(p,PREV||p)||[];
   // Najczestsza pomylka: wklejone same obliczenia, bez `return [...]`.
   if(!out.length&&src.trim()&&!/\breturn\b/.test(src))
    hint='reguly nic nie zwracaja - brakuje na koncu: return [ [opis, oczekiwane, odczytane, czyOK] ];';
   $('rerr').textContent=hint}
  catch(e){$('rerr').textContent='blad regul: '+e.message}
  PREV=p;
  $('res').innerHTML=out.length?'<tr><th>regula</th><th>oczekiwane</th><th>odczytane</th><th></th></tr>'+
   out.map(r=>`<tr><td>${r[0]}</td><td style="text-align:right">${fmt(+r[1])}</td>
    <td style="text-align:right">${fmt(+r[2])}</td>
    <td class="${nd(r)?'muted':r[3]?'ok':'bad'}">${nd(r)?'brak danych':r[3]?'OK':'ROZJAZD'}</td></tr>`).join('')
   :'<tr><td class="muted">profil bez regul albo brak punktow</td></tr>';
 }catch(e){}
 setTimeout(tick,1500)}
loadProfiles().then(tick);
</script></body></html>"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', nargs='?', const='', metavar='NAZWA',
                    help='symulowane urzadzenie z sims/NAZWA.py zamiast realnego BACnet')
    ap.add_argument('--ip', help='lokalny interfejs BACnet, np. 192.168.1.10/24')
    ap.add_argument('--port', type=int, default=8342)
    ARGS = ap.parse_args()
    if ARGS.sim is not None:
        name = load_sim(ARGS.sim)
        STATE.update(mode='sim', device=f'symulator: {name}')
        threading.Thread(target=sim_loop, daemon=True).start()
        gdzie = f'SYMULACJA {name}'
    else:
        bacnet_init(ARGS.ip)
        gdzie = 'BACnet'
    print(f'bacnet-check: http://localhost:{ARGS.port}  ({gdzie})')
    ThreadingHTTPServer(('0.0.0.0', ARGS.port), H).serve_forever()
