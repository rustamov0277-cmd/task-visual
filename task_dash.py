"""
Вазифалар дашборди — Sheets → HTML → GitHub Pages.
Cron: ҳар 10 дақиқада.

    cd /root/task_bot && source start.sh && python3 task_dash.py
"""

import os, sys, json, base64, ssl, re, logging
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials

TZ = timezone(timedelta(hours=5))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

SHEET_ID = os.environ.get("TASK_SHEET_ID", "")
SA_JSON  = os.environ.get("TASK_SA_JSON", "/root/task_bot/service_account.json")
TASKS_WS = "Vazifalar"
RECUR_WS = "Takroriy"

GH_TOKEN = (os.environ.get("TASK_GITHUB_TOKEN", "")
            or os.environ.get("DASH_GITHUB_TOKEN", "")
            or os.environ.get("RS_GITHUB_TOKEN", ""))
GH_USER  = os.environ.get("TASK_GITHUB_USER", "rustamov0277-cmd")
GH_REPO  = os.environ.get("TASK_GITHUB_REPO", "task-visual")
GH_FILE  = os.environ.get("TASK_GITHUB_FILE", "index.html")
OUT_FILE = "/root/task_bot/index.html"


def _gc():
    creds = Credentials.from_service_account_file(
        SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return gspread.authorize(creds)


def parse_dl(text):
    if not text:
        return None
    s = str(text).strip()
    hh, mm = 23, 59
    t = re.search(r"(\d{1,2}):(\d{2})", s)
    if t:
        hh, mm = int(t.group(1)), int(t.group(2))
        s = s[:t.start()].strip()
    d = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", s)
    if not d:
        return None
    day, mo = int(d.group(1)), int(d.group(2))
    y = d.group(3)
    y = (int(y) + 2000 if int(y) < 100 else int(y)) if y else datetime.now(TZ).year
    try:
        return datetime(y, mo, day, hh, mm, tzinfo=TZ)
    except ValueError:
        return None


def status_of(deadline, holat):
    if holat == "Bajarildi":
        return "done"
    if holat == "Rad etildi":
        return "rejected"
    if str(holat or "").startswith("Yopildi"):
        return "closed"
    dt = parse_dl(deadline)
    if not dt:
        return "none"
    now = datetime.now(TZ)
    if now > dt:
        return "overdue"
    if (dt.date() - now.date()).days == 0:
        return "today"
    return "open"


def collect():
    book = _gc().open_by_key(SHEET_ID)
    tasks, recur = [], []

    vals = book.worksheet(TASKS_WS).get_all_values()
    for r in vals[1:]:
        if len(r) < 7 or not (r[1] or "").strip():
            continue
        g = lambda i: (r[i] if i < len(r) else "") or ""
        holat = g(6)
        dl = g(4)
        done_at = g(17)
        late = None
        d1, d2 = parse_dl(dl), parse_dl(done_at)
        if holat == "Bajarildi" and d1 and d2:
            late = d2.date() > d1.date()
        baho = g(14).split("/")[0].strip()
        tasks.append({
            "id": g(0), "what": g(1), "who": g(2).strip(), "un": g(3),
            "dl": dl, "price": g(5), "holat": holat,
            "tasdiq": g(7), "by": g(8), "created": g(9),
            "sabab": g(12), "dalil": g(13),
            "baho": int(baho) if baho.isdigit() else None,
            "izoh": g(15), "done_at": done_at,
            "st": status_of(dl, holat), "late": late,
        })

    try:
        for r in book.worksheet(RECUR_WS).get_all_values()[1:]:
            if len(r) < 5 or not (r[1] or "").strip():
                continue
            g = lambda i: (r[i] if i < len(r) else "") or ""
            faol = g(9).lower() in ("ha", "yes", "1", "faol", "")
            recur.append({
                "what": g(1), "who": g(2).strip(), "takror": g(4), "kun": g(5),
                "price": g(6), "last": g(8), "faol": faol, "soat": g(10) or "08:00",
                "fails": g(11), "pauza": g(12),
            })
    except Exception as e:
        log.error("recur: %s", e)

    log.info("Вазифа: %d · Такрорий: %d", len(tasks), len(recur))
    return tasks, recur


def build_people(tasks):
    P = {}
    for t in tasks:
        if not t["who"]:
            continue
        p = P.setdefault(t["who"], {"name": t["who"], "total": 0, "ontime": 0,
                                    "late": 0, "overdue": 0, "rejected": 0,
                                    "closed": 0, "open": 0, "today": 0,
                                    "check": 0, "stars": []})
        p["total"] += 1
        if t["holat"] == "Bajarildi":
            if t["late"]:
                p["late"] += 1
            else:
                p["ontime"] += 1
            if t["baho"]:
                p["stars"].append(t["baho"])
        elif t["st"] == "rejected":
            p["rejected"] += 1
        elif t["st"] == "closed":
            p["closed"] += 1
        else:
            if t["holat"] == "Tekshiruvda":
                p["check"] += 1
            if t["st"] == "overdue":
                p["overdue"] += 1
            elif t["st"] == "today":
                p["today"] += 1
            else:
                p["open"] += 1

    out = []
    for p in P.values():
        base = p["ontime"] + p["late"] + p["overdue"] + p["rejected"] + p["closed"]
        p["ontime_pct"] = round(p["ontime"] / base * 100, 1) if base else None
        p["avg_star"] = round(sum(p["stars"]) / len(p["stars"]), 1) if p["stars"] else None
        disc = p["ontime_pct"] or 0
        qual = ((p["avg_star"] - 1) / 4 * 100) if p["avg_star"] else disc
        p["score"] = round(disc * 0.7 + qual * 0.3, 1) if base else 0
        p.pop("stars", None)
        out.append(p)
    out.sort(key=lambda x: (-x["score"], -x["ontime"]))
    return out


HTML = """<!DOCTYPE html><html lang="uz"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Вазифалар — назорат</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#f4f6fa;--card:#fff;--line:#e4e9f0;--line2:#eef2f7;--txt:#0f172a;--mut:#64748b;
--mut2:#94a3b8;--blue:#2563eb;--gtx:#15803d;--gbg:#dcfce7;--atx:#b45309;--abg:#fef3c7;
--rtx:#b91c1c;--rbg:#fee2e2}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Montserrat',-apple-system,sans-serif;background:var(--bg);color:var(--txt);
padding:20px;line-height:1.5;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.wrap{max-width:1400px;margin:0 auto}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;
background:linear-gradient(120deg,#2563eb,#7c3aed);color:#fff;padding:18px 22px;
border-radius:18px;box-shadow:0 6px 20px rgba(37,99,235,.22)}
h1{font-size:20px;font-weight:800}
.top .s{font-size:13px;opacity:.92;font-weight:500}
h2{font-size:12px;letter-spacing:.07em;color:var(--mut);font-weight:700;
text-transform:uppercase;margin:26px 0 12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:15px 17px;
box-shadow:0 1px 3px rgba(15,23,42,.05)}
.kpi .l{color:var(--mut);font-size:11.5px;font-weight:600;text-transform:uppercase;margin-bottom:7px}
.kpi .v{font-size:25px;font-weight:800}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.btn{background:var(--card);border:1px solid var(--line);color:var(--mut);padding:9px 16px;
border-radius:11px;font-size:14px;cursor:pointer;font-weight:600;font-family:inherit}
.btn:hover{border-color:#c7d2e0;color:var(--txt)}
.btn.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:2px;
overflow-x:auto;margin-top:6px;box-shadow:0 1px 3px rgba(15,23,42,.05)}
table{width:100%;border-collapse:collapse;min-width:820px}
th{text-align:right;color:var(--mut);font-size:11px;font-weight:700;padding:13px 11px;
text-transform:uppercase;border-bottom:2px solid var(--line);white-space:nowrap;background:#fafbfd}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
td{padding:12px 11px;text-align:right;font-size:14px;border-bottom:1px solid var(--line2);font-weight:500}
tbody tr:hover{background:#f8fafc}
tbody tr.clk{cursor:pointer}
td.n{font-weight:700}
td.r{color:var(--mut2);font-size:12px;width:36px;text-align:center;font-weight:600}
.bd{display:inline-block;padding:3px 10px;border-radius:8px;font-size:12px;font-weight:700;white-space:nowrap}
.g{background:var(--gbg);color:var(--gtx)}.a{background:var(--abg);color:var(--atx)}
.r2{background:var(--rbg);color:var(--rtx)}.b{background:#dbeafe;color:#1d4ed8}
.gr{background:#f1f5f9;color:var(--mut)}
.tsk{font-weight:600}.sub{font-size:12px;color:var(--mut);font-weight:500;margin-top:2px}
.empty{color:var(--mut);text-align:center;padding:36px;font-weight:600}
.note{color:var(--mut);font-size:13px;margin-top:14px;padding:12px 16px;background:#eef4ff;
border-left:4px solid var(--blue);border-radius:0 10px 10px 0;font-weight:500}
.foot{color:var(--mut2);font-size:12px;margin-top:24px;text-align:center;font-weight:500}
@media(max-width:820px){
 body{padding:12px}.kpis{grid-template-columns:1fr 1fr;gap:9px}.kpi .v{font-size:20px}
 .panel{background:transparent;border:none;padding:0;overflow:visible;box-shadow:none}
 table{min-width:0}table,thead,tbody,tr,td{display:block}thead{display:none}
 tbody tr{background:var(--card);border:1px solid var(--line);border-radius:14px;
  margin-bottom:10px;padding:4px 0;box-shadow:0 1px 3px rgba(15,23,42,.05)}
 td{display:flex;justify-content:space-between;gap:12px;text-align:right;border:none;
  padding:8px 16px;white-space:normal}
 td:before{content:attr(data-l);color:var(--mut);font-size:11.5px;text-align:left;
  text-transform:uppercase;font-weight:600;flex:0 0 auto}
 td.r{display:none}
 td.n{font-size:15px;font-weight:800;padding:12px 16px;border-bottom:1px solid var(--line);
  display:block;text-align:left}
 td.n:before{content:''}
}
</style></head><body><div class="wrap">

<div class="top">
 <div><h1>📋 Вазифалар — назорат</h1><div class="s">AI Ассистент-Менежер</div></div>
 <div class="s" style="text-align:right">Янгиланди: <b id="upd"></b></div>
</div>

<h2>Умумий</h2>
<div class="kpis" id="kpis"></div>

<h2>Ходимлар</h2>
<div class="panel" id="ppl"></div>

<h2 id="th">Вазифалар</h2>
<div class="bar" id="flt"></div>
<div class="panel" id="tbl"></div>

<h2>Такрорий вазифалар</h2>
<div class="panel" id="rec"></div>

<div class="note">Балл = 70% интизом (ўз вақтида) + 30% сифат (⭐).
Ходим устига босинг — фақат ўша ходимнинг вазифалари кўринади.</div>
<div class="foot">Манба: Google Sheets · Ҳар 10 дақиқада янгиланади</div>
</div>

<script>
var D=__PAYLOAD__;
var FLT='open', WHO=null;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function n0(v){return v==null?'—':v.toLocaleString('ru-RU')}
function pc(v){return v==null?'—':v+'%'}
function bd(t,c){return '<span class="bd '+c+'">'+t+'</span>'}

var STL={done:['✅ Бажарилди','g'],overdue:['🔴 Муддат ўтди','r2'],
 today:['🟡 Бугун','a'],open:['🔵 Очиқ','b'],rejected:['🚫 Рад','gr'],
 closed:['⛔ Ёпилди','gr'],none:['—','gr']};

function kpis(){
 var t=D.tasks,k={done:0,overdue:0,today:0,open:0,check:0,rej:0,closed:0};
 for(var i=0;i<t.length;i++){var x=t[i];
  if(x.holat==='Bajarildi')k.done++;
  else if(x.st==='rejected')k.rej++;
  else if(x.st==='closed')k.closed++;
  else{if(x.holat==='Tekshiruvda')k.check++;
   if(x.st==='overdue')k.overdue++;else if(x.st==='today')k.today++;else k.open++}}
 var st=[],n=0;
 for(var i=0;i<t.length;i++)if(t[i].baho){st.push(t[i].baho);n++}
 var avg=n?(st.reduce(function(a,b){return a+b},0)/n).toFixed(1):'—';
 function K(l,v,c){return '<div class="kpi"><div class="l">'+l+'</div><div class="v"'+
  (c?' style="color:'+c+'"':'')+'>'+v+'</div></div>'}
 document.getElementById('kpis').innerHTML=
  K('Жами',t.length)+K('Бажарилди',k.done,'#15803d')+
  K('Муддат ўтди',k.overdue,'#b91c1c')+K('Бугун',k.today,'#b45309')+
  K('Очиқ',k.open,'#1d4ed8')+K('Текширувда',k.check)+
  K('Ёпилди',k.closed)+K('Ўртача баҳо','⭐'+avg);
}

function people(){
 var p=D.people,el=document.getElementById('ppl');
 if(!p.length){el.innerHTML='<div class="empty">Маълумот йўқ</div>';return}
 var h='<table><thead><tr><th>#</th><th>Ходим</th><th>Балл</th><th>Ўз вақтида</th>'+
  '<th>Кечикиб</th><th>Муддат ўтган</th><th>Ёпилган</th><th>Очиқ</th>'+
  '<th>Интизом</th><th>Баҳо</th></tr></thead><tbody>';
 for(var i=0;i<p.length;i++){var s=p[i];
  var sc=s.score>=85?'g':s.score>=60?'a':'r2';
  h+='<tr class="clk" data-w="'+esc(s.name)+'"><td class="r">'+(i+1)+'</td>'+
   '<td class="n">'+esc(s.name)+'</td>'+
   '<td data-l="Балл">'+bd(s.score,sc)+'</td>'+
   '<td data-l="Ўз вақтида">'+n0(s.ontime)+'</td>'+
   '<td data-l="Кечикиб">'+(s.late?bd(s.late,'a'):'—')+'</td>'+
   '<td data-l="Муддат ўтган">'+(s.overdue?bd(s.overdue,'r2'):'—')+'</td>'+
   '<td data-l="Ёпилган">'+(s.closed?bd(s.closed,'gr'):'—')+'</td>'+
   '<td data-l="Очиқ">'+n0(s.open+s.today+s.check)+'</td>'+
   '<td data-l="Интизом">'+pc(s.ontime_pct)+'</td>'+
   '<td data-l="Баҳо">'+(s.avg_star?'⭐'+s.avg_star:'—')+'</td></tr>';
 }
 el.innerHTML=h+'</tbody></table>';
 el.querySelectorAll('tr.clk').forEach(function(tr){tr.onclick=function(){
  var w=tr.getAttribute('data-w');WHO=(WHO===w)?null:w;tasks()}});
}

function filters(){
 var f=[['all','Барчаси'],['open','Очиқ'],['overdue','Муддат ўтди'],
        ['check','Текширувда'],['done','Бажарилди'],['closed','Ёпилган']];
 var h='';
 for(var i=0;i<f.length;i++)h+='<button class="btn'+(FLT===f[i][0]?' on':'')+
  '" data-f="'+f[i][0]+'">'+f[i][1]+'</button>';
 if(WHO)h+='<button class="btn on" id="clr">✕ '+esc(WHO)+'</button>';
 var el=document.getElementById('flt');el.innerHTML=h;
 el.querySelectorAll('button[data-f]').forEach(function(b){
  b.onclick=function(){FLT=b.getAttribute('data-f');tasks()}});
 var c=document.getElementById('clr');
 if(c)c.onclick=function(){WHO=null;tasks()};
}

function tasks(){
 filters();
 document.getElementById('th').textContent='Вазифалар'+(WHO?' — '+WHO:'');
 var r=D.tasks.filter(function(x){
  if(WHO&&x.who!==WHO)return false;
  if(FLT==='all')return true;
  if(FLT==='done')return x.holat==='Bajarildi';
  if(FLT==='check')return x.holat==='Tekshiruvda';
  if(FLT==='overdue')return x.st==='overdue';
  if(FLT==='closed')return x.st==='closed';
  if(FLT==='open')return x.holat!=='Bajarildi'&&x.st!=='rejected'&&x.st!=='closed';
  return true});
 var el=document.getElementById('tbl');
 if(!r.length){el.innerHTML='<div class="empty">Вазифа йўқ</div>';return}
 var ord={overdue:0,today:1,open:2,none:3,rejected:4,closed:5,done:6};
 r.sort(function(a,b){var d=(ord[a.st]||9)-(ord[b.st]||9);if(d)return d;
  return (a.dl||'')<(b.dl||'')?-1:1});
 var h='<table><thead><tr><th>#</th><th>Вазифа</th><th>Ходим</th><th>Муддат</th>'+
  '<th>Ҳолат</th><th>Тасдиқ</th><th>Цена слова</th><th>Баҳо</th></tr></thead><tbody>';
 for(var i=0;i<r.length;i++){var x=r[i];
  var s=STL[x.st]||STL.none, extra='';
  if(x.holat==='Tekshiruvda')s=['🔍 Текширувда','b'];
  if(x.holat==='Qayta bajarilsin')s=['🔁 Қайта','a'];
  if(x.late)extra=' <span class="bd a">кечикиб</span>';
  h+='<tr><td class="r">'+(i+1)+'</td>'+
   '<td class="n"><div class="tsk">'+esc(x.what)+'</div>'+
   (x.sabab?'<div class="sub">💬 '+esc(x.sabab)+'</div>':'')+'</td>'+
   '<td data-l="Ходим">'+esc(x.who)+'</td>'+
   '<td data-l="Муддат">'+esc(x.dl)+'</td>'+
   '<td data-l="Ҳолат">'+bd(s[0],s[1])+extra+'</td>'+
   '<td data-l="Тасдиқ">'+(x.tasdiq==='Ha'?'✅':'<span class="bd a">йўқ</span>')+'</td>'+
   '<td data-l="Цена слова">'+esc(x.price||'—')+'</td>'+
   '<td data-l="Баҳо">'+(x.baho?'⭐'+x.baho:'—')+'</td></tr>';
 }
 el.innerHTML=h+'</tbody></table>';
}

function recur(){
 var r=D.recur,el=document.getElementById('rec');
 if(!r.length){el.innerHTML='<div class="empty">Такрорий вазифа йўқ</div>';return}
 var W={kunlik:'ҳар куни',haftalik:'ҳафтада',oylik:'ойда'};
 var h='<table><thead><tr><th>#</th><th>Вазифа</th><th>Ходим</th><th>Такрор</th>'+
  '<th>Соат</th><th>Ҳолат</th><th>Охирги</th></tr></thead><tbody>';
 for(var i=0;i<r.length;i++){var x=r[i];
  var st=x.faol?bd('✅ Фаол','g'):bd('⏸ Тўхтаган','r2');
  h+='<tr><td class="r">'+(i+1)+'</td><td class="n"><div class="tsk">'+esc(x.what)+'</div>'+
   (!x.faol&&x.pauza?'<div class="sub">⛔ '+esc(x.pauza)+'</div>':'')+'</td>'+
   '<td data-l="Ходим">'+esc(x.who)+'</td>'+
   '<td data-l="Такрор">'+esc(W[(x.takror||'').toLowerCase()]||x.takror)+'</td>'+
   '<td data-l="Соат">'+esc(x.soat)+'</td>'+
   '<td data-l="Ҳолат">'+st+'</td>'+
   '<td data-l="Охирги">'+esc(x.last||'—')+'</td></tr>';
 }
 el.innerHTML=h+'</tbody></table>';
}

document.getElementById('upd').textContent=D.updated;
kpis();people();tasks();recur();
setTimeout(function(){location.reload()},600000);
</script></body></html>"""


def push_github(html):
    if not GH_TOKEN:
        log.warning("GitHub токен йўқ — юкланмади")
        return False
    api = "https://api.github.com/repos/%s/%s/contents/%s" % (GH_USER, GH_REPO, GH_FILE)
    hd = {"Authorization": "token " + GH_TOKEN,
          "Accept": "application/vnd.github.v3+json", "User-Agent": "task-dash"}
    ctx = ssl._create_unverified_context()
    sha = None
    try:
        with urllib.request.urlopen(urllib.request.Request(api, headers=hd), context=ctx) as r:
            sha = json.loads(r.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.error("SHA: %s", e)
    pl = {"message": "tasks " + datetime.now(TZ).strftime("%d.%m %H:%M"),
          "content": base64.b64encode(html.encode()).decode()}
    if sha:
        pl["sha"] = sha
    try:
        req = urllib.request.Request(api, data=json.dumps(pl).encode(),
                                     headers=hd, method="PUT")
        with urllib.request.urlopen(req, context=ctx) as r:
            log.info("GitHub push OK: %s", r.status)
            return True
    except Exception as e:
        log.error("push: %s", e)
        return False


if __name__ == "__main__":
    if not SHEET_ID:
        sys.exit("❌ TASK_SHEET_ID ўрнатилмаган")
    tasks, recur = collect()
    payload = {"tasks": tasks, "recur": recur, "people": build_people(tasks),
               "updated": datetime.now(TZ).strftime("%d.%m.%Y %H:%M")}
    html = HTML.replace("__PAYLOAD__",
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML: %d КБ", len(html) // 1024)
    push_github(html)
    log.info("✅ Тайёр")