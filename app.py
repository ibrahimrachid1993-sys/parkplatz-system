import os, json, re, uuid, csv, base64, logging, requests
from datetime import datetime
from io import BytesIO, StringIO
from flask import Flask, render_template_string, request, jsonify, make_response
from PIL import Image, ImageEnhance

app = Flask(__name__)

# ========= KONFIG =========
ROWS = 4
COLS = 4
AREAS = ROWS * COLS
MAX_CAPACITY = 650

DATA_FILE = "data.json"
HISTORY_FILE = "history.json"

# OCR (ocr.space)
OCR_API_KEY = "K86896712788957"   # dein Key
OCR_API_URL = "https://api.ocr.space/parse/image"

# Gebühren
FREE_DAYS = 7
BASE_FEE = 20.00
DAILY_FEE = 4.50

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= HELPERS =========
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Load error {path}: {e}")
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def preprocess_image(image: Image.Image) -> Image.Image:
    if image.mode != "L":
        image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = ImageEnhance.Brightness(image).enhance(1.15)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    return image

VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
LAGER_RE = re.compile(r"\b[A-Z]{2}[0-9]{5}\b")  # LK12345

def validate_vin(v):
    v = (v or "").upper().strip()
    if len(v) != 17:
        return None
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v):
        return None
    return v

def validate_lager(l):
    l = (l or "").upper().strip().replace(" ", "").replace("-", "")
    if re.fullmatch(r"[A-Z]{2}[0-9]{5}", l):
        return l
    m = re.search(r"([A-Z]{2}[0-9]{5})", l)
    return m.group(1) if m else None

def parse_extras_from_notes(notes: str):
    extras = []
    if not notes:
        return extras
    # erkennt z.B. "+25€", "25€", "4.5€", "4,50€"
    m = re.search(r"([+-]?\d+(?:[.,]\d+)?)\s*€", notes)
    if m:
        val = float(m.group(1).replace(",", "."))
        extras.append({
            "description": notes[:60],
            "cost": round(val, 2),
            "date": datetime.now().strftime("%Y-%m-%d")
        })
    return extras

def calculate_fees(ready_date, in_time, extras=None):
    extras = extras or []
    extras_total = sum(float(e.get("cost", 0)) for e in extras)

    if not ready_date:
        return {
            "overdue_days": 0, "base_fee": 0, "daily_fee": 0, "daily_fee_total": 0,
            "total_fee": 0, "extras_total": round(extras_total, 2),
            "grand_total": round(extras_total, 2), "status": "kein_termin"
        }

    try:
        ready = datetime.strptime(ready_date, "%Y-%m-%d")
        today = datetime.now()
        days_since_ready = (today - ready).days

        if days_since_ready <= FREE_DAYS:
            return {
                "overdue_days": 0, "base_fee": 0, "daily_fee": DAILY_FEE, "daily_fee_total": 0,
                "total_fee": 0, "extras_total": round(extras_total, 2),
                "grand_total": round(extras_total, 2), "status": "innerhalb_frist",
                "days_since_ready": days_since_ready
            }

        overdue_days = days_since_ready - FREE_DAYS
        daily_fee_total = overdue_days * DAILY_FEE
        total_fee = BASE_FEE + daily_fee_total
        grand_total = total_fee + extras_total

        if overdue_days <= 3:
            status = "leicht_ueberzogen"
        elif overdue_days <= 7:
            status = "ueberzogen"
        else:
            status = "stark_ueberzogen"

        return {
            "overdue_days": overdue_days,
            "base_fee": BASE_FEE,
            "daily_fee": DAILY_FEE,
            "daily_fee_total": round(daily_fee_total, 2),
            "total_fee": round(total_fee, 2),
            "extras_total": round(extras_total, 2),
            "grand_total": round(grand_total, 2),
            "status": status,
            "days_since_ready": days_since_ready
        }
    except:
        return {
            "overdue_days": 0, "base_fee": 0, "daily_fee": 0, "daily_fee_total": 0,
            "total_fee": 0, "extras_total": round(extras_total, 2),
            "grand_total": round(extras_total, 2), "status": "fehler"
        }

def status_color(fees, ready_date):
    if not ready_date:
        return "#9ca3af"
    st = fees.get("status")
    if st == "innerhalb_frist":
        return "#22c55e"
    if st == "leicht_ueberzogen":
        return "#facc15"
    if st == "ueberzogen":
        return "#fb923c"
    if st == "stark_ueberzogen":
        return "#ef4444"
    return "#9ca3af"

def find_car_by_id(car_id):
    for z, cars in zones.items():
        for c in cars:
            if c["id"] == car_id:
                return z, c
    return None, None

# ========= DATA =========
zones = load_json(DATA_FILE, {str(i): [] for i in range(AREAS)})
history = load_json(HISTORY_FILE, [])

# ========= UI =========
HTML = r"""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Parkplatz</title>
<style>
  :root{
    --bg:#f3f6ff; --card:#ffffff; --stroke:#e6ebff; --text:#0f172a;
    --muted:#64748b; --brand:#3b82f6; --brand2:#6366f1;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;color:var(--text)}
  .wrap{max-width:980px;margin:0 auto;padding:14px}
  .hero{
    background:linear-gradient(135deg,#e9f1ff, #eef2ff);
    border:1px solid var(--stroke);
    border-radius:18px;
    padding:14px 14px;
    box-shadow:0 10px 30px rgba(15,23,42,.06);
  }
  .title{display:flex;gap:10px;align-items:center;font-weight:800;font-size:18px}
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:10px}
  .pill{
    background:rgba(255,255,255,.8);
    border:1px solid var(--stroke);
    border-radius:14px;
    padding:10px;
    display:flex;justify-content:space-between;align-items:center;
  }
  .pill b{font-size:20px}
  .card{
    margin-top:12px;background:var(--card);
    border:1px solid var(--stroke);
    border-radius:18px;padding:14px;
    box-shadow:0 10px 30px rgba(15,23,42,.05);
  }
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .btn{
    border:0;border-radius:14px;padding:12px 14px;
    font-weight:700;cursor:pointer;
  }
  .btn.primary{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff}
  .btn.light{background:#eef2ff;color:#111827;border:1px solid var(--stroke)}
  .btn.danger{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}
  .btn.ok{background:#dcfce7;color:#14532d;border:1px solid #bbf7d0}
  input,select,textarea{
    width:100%;padding:12px 12px;border-radius:14px;border:1px solid var(--stroke);
    background:#fff;font-size:16px;outline:none;
  }
  input:focus,select:focus,textarea:focus{border-color:#c7d2fe;box-shadow:0 0 0 4px rgba(99,102,241,.12)}
  .mapHead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
  .badge{background:#eef2ff;border:1px solid var(--stroke);border-radius:999px;padding:8px 10px;font-weight:700}
  canvas{
    width:100%;
    height:340px; /* WICHTIG: sonst 0 Höhe auf iPhone */
    display:block;
    border-radius:16px;
    background:#eef2ff;
    border:1px solid var(--stroke);
  }
  .hint{color:var(--muted);font-size:13px;margin-top:6px}
  details{border-top:1px solid var(--stroke);padding-top:10px;margin-top:10px}
  summary{cursor:pointer;font-weight:800}
  .car{
    border:1px solid var(--stroke);border-radius:16px;padding:12px;margin-top:10px;
    background:#fbfdff;
  }
  .carTop{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
  .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  .tag{border-radius:999px;padding:6px 10px;font-weight:800;color:#fff;font-size:12px;white-space:nowrap}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
  .small{font-size:12px;color:var(--muted)}
  .modal{
    position:fixed;inset:0;background:rgba(15,23,42,.55);
    display:none;align-items:center;justify-content:center;padding:14px;z-index:10;
  }
  .modal .box{
    width:min(640px,96vw);max-height:86vh;overflow:auto;
    background:#fff;border-radius:18px;border:1px solid var(--stroke);
    padding:14px;
  }
  .modal h2{margin:0 0 10px 0}
  .hr{height:1px;background:var(--stroke);margin:12px 0}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <div class="title">🅿️ Parkplatz <span class="small">Tippe auf eine Zone im Bild → hinzufügen. Suche markiert Zone gelb.</span></div>
    <div class="stats">
      <div class="pill"><span>Belegt:</span><b>{{ total }}</b></div>
      <div class="pill"><span>Frei:</span><b>{{ avail }}</b></div>
      <div class="pill"><span>Kapazität:</span><b>{{ maxcap }}</b></div>
    </div>
  </div>

  <div class="card">
    <div class="mapHead">
      <div style="font-weight:900">🗺️ Karte</div>
      <div class="badge">Zonen 1–{{ areas }}</div>
    </div>
    <canvas id="map"></canvas>
    <div class="hint">Wenn du das Bild nicht siehst: prüfe, ob <b>static/parking_lot.png</b> im Repo liegt.</div>

    <div class="row" style="margin-top:10px">
      <div style="flex:1;min-width:220px">
        <input id="q" placeholder="Suche VIN oder Lagernur"/>
      </div>
      <button class="btn light" onclick="doSearch()">🔍 Suchen</button>
      <button class="btn primary" onclick="openAdd()">➕ Einparken</button>
    </div>
  </div>

  <div class="card">
    <div style="font-weight:900;margin-bottom:8px">📦 CSV Export <span class="small">Excel-fertig</span></div>
    <div class="row">
      <button class="btn primary" onclick="window.location='/api/export_all'">Alles exportieren</button>
    </div>
    <div class="row" style="margin-top:10px">
      <div style="flex:1;min-width:150px">
        <div class="small">Von (Auslagerung)</div>
        <input id="from" type="date"/>
      </div>
      <div style="flex:1;min-width:150px">
        <div class="small">Bis (Auslagerung)</div>
        <input id="to" type="date"/>
      </div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn light" onclick="exportHistoryRange()">History Zeitraum exportieren</button>
    </div>
  </div>

  <div class="card">
    <div style="font-weight:900;margin-bottom:8px">🚗 Zonen <span class="small">Einklappbar</span></div>
    {% for zi in range(areas) %}
      {% set cars = zones[zi|string] %}
      <details {% if zi==0 %}open{% endif %}>
        <summary>Zone {{ zi+1 }} <span class="small">({{ cars|length }} Fahrzeuge)</span></summary>
        {% if cars|length == 0 %}
          <div class="small" style="margin-top:10px">leer</div>
        {% endif %}
        {% for car in cars %}
          {% set fees = fees_map.get(car.id) %}
          {% set col = color_map.get(car.id) %}
          <div class="car">
            <div class="carTop">
              <div>
                <div class="mono" style="font-weight:900">📋 {{ car.vin }}</div>
                <div class="small">Lagernr: <b>{{ car.lager }}</b></div>
                <div class="small">Eingelagert: <b>{{ car.in_time }}</b></div>
                {% if car.ready_date %}
                  <div class="small">Bereitstellung: <b>{{ car.ready_date }} {{ car.ready_time or "" }}</b></div>
                {% endif %}
              </div>
              <div class="tag" style="background:{{ col }};">
                {% if fees.status == "kein_termin" %}Kein Termin{% elif fees.status=="innerhalb_frist" %}✅ OK{% else %}⚠️ {{ fees.overdue_days }} Tage{% endif %}
              </div>
            </div>

            {% if car.ready_date %}
              <div class="grid2">
                <div class="small">Grundgebühr: <b>{{ fees.base_fee }}€</b></div>
                <div class="small">Tagesgebühr: <b>{{ fees.daily_fee_total }}€</b></div>
                <div class="small">Extras: <b>{{ fees.extras_total }}€</b></div>
                <div class="small">Gesamt: <b>{{ fees.grand_total }}€</b></div>
              </div>
            {% endif %}

            <div class="row" style="margin-top:10px">
              <button class="btn light" onclick="openMove('{{ car.id }}', {{ zi }})">🔁 Move</button>
              <button class="btn danger" onclick="removeCar('{{ car.id }}')">🚚 Ausparken</button>
            </div>
          </div>
        {% endfor %}
      </details>
    {% endfor %}
  </div>

</div>

<!-- MODAL: ADD -->
<div class="modal" id="addModal">
  <div class="box">
    <div class="row" style="justify-content:space-between">
      <h2>➕ Auto einparken</h2>
      <button class="btn light" onclick="closeModal('addModal')">✖</button>
    </div>

    <div class="small">Zone</div>
    <select id="zoneSel">
      <option value="">-- Zone wählen --</option>
      {% for i in range(areas) %}
        <option value="{{ i }}">Zone {{ i+1 }}</option>
      {% endfor %}
    </select>

    <div class="hr"></div>

    <div class="row" style="justify-content:space-between">
      <div style="font-weight:900">📷 Foto/OCR</div>
      <button class="btn light" onclick="toggleManual()">✍️ Manuell</button>
    </div>

    <div id="cameraBlock" style="margin-top:10px">
      <input id="imgFile" type="file" accept="image/*" capture="environment"/>
      <div class="small" style="margin-top:6px">Tipp: VIN + Lagernr groß & scharf fotografieren.</div>
    </div>

    <div id="manualBlock" style="display:none;margin-top:10px">
      <div class="small">VIN (17 Zeichen)</div>
      <input id="vinIn" placeholder="W0L00000000000000" maxlength="17" />
      <div class="small" style="margin-top:8px">Lagernummer (LK12345)</div>
      <input id="lagerIn" placeholder="LK12345" maxlength="7" />
    </div>

    <div class="hr"></div>

    <div class="row">
      <label class="small" style="display:flex;gap:8px;align-items:center">
        <input id="hasReady" type="checkbox" onchange="toggleReady()"/>
        Bereitstellungstermin setzen (optional)
      </label>
    </div>

    <div id="readyBlock" style="display:none;margin-top:10px">
      <div class="small">Datum</div>
      <input id="readyDate" type="date"/>
      <div class="small" style="margin-top:8px">Uhrzeit (optional)</div>
      <input id="readyTime" type="time"/>
    </div>

    <div class="small" style="margin-top:10px">Notizen / Extras (z.B. “Booster +25€”)</div>
    <textarea id="notes" rows="2" placeholder="Optional..."></textarea>

    <div class="row" style="margin-top:12px">
      <button class="btn primary" style="flex:1" onclick="submitAdd()">✅ Speichern</button>
    </div>

    <div id="addMsg" class="small" style="margin-top:10px;color:#334155"></div>
  </div>
</div>

<!-- MODAL: MOVE -->
<div class="modal" id="moveModal">
  <div class="box">
    <div class="row" style="justify-content:space-between">
      <h2>🔁 Fahrzeug verschieben</h2>
      <button class="btn light" onclick="closeModal('moveModal')">✖</button>
    </div>
    <div class="small">Ziel-Zone</div>
    <select id="moveZone">
      {% for i in range(areas) %}
        <option value="{{ i }}">Zone {{ i+1 }}</option>
      {% endfor %}
    </select>
    <div class="row" style="margin-top:12px">
      <button class="btn primary" style="flex:1" onclick="doMove()">Verschieben</button>
    </div>
  </div>
</div>

<script>
const zonesData = {{ zones | tojson }};
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");

let highlightZone = null;
let addManual = false;
let movingCarId = null;

function fitCanvas() {
  // real pixel size (für scharfes Canvas)
  const cssH = 340;
  const cssW = canvas.clientWidth;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(cssW * dpr);
  canvas.height = Math.floor(cssH * dpr);
  canvas.style.height = cssH + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

const img = new Image();
img.src = "/static/parking_lot.png";
img.onload = () => { redraw(); };
img.onerror = () => {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.fillStyle="#111";
  ctx.font="bold 16px system-ui";
  ctx.fillText("Bild fehlt: /static/parking_lot.png", 10, 30);
};

function redraw() {
  fitCanvas();
  const w = canvas.clientWidth;
  const h = parseFloat(canvas.style.height);

  ctx.clearRect(0,0,w,h);
  ctx.drawImage(img, 0, 0, w, h);

  const cellW = w / {{ cols }};
  const cellH = h / {{ rows }};

  // Overlays + Grid
  let z = 1;
  for(let r=0;r<{{ rows }};r++){
    for(let c=0;c<{{ cols }};c++){
      const idx = r*{{ cols }}+c;
      const cars = zonesData[String(idx)] || [];
      if(cars.length>0){
        ctx.fillStyle = "rgba(59,130,246,0.22)";
        ctx.fillRect(c*cellW, r*cellH, cellW, cellH);
      }
      ctx.strokeStyle = "rgba(15,23,42,0.25)";
      ctx.lineWidth = 2;
      ctx.strokeRect(c*cellW, r*cellH, cellW, cellH);

      // Zone label
      ctx.fillStyle = "rgba(15,23,42,0.8)";
      ctx.font = "bold 14px system-ui";
      ctx.fillText("Z"+z, c*cellW + 8, r*cellH + 20);

      // count
      if(cars.length>0){
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.font = "900 18px system-ui";
        ctx.fillText(String(cars.length), c*cellW + cellW - 22, r*cellH + 24);
      }
      z++;
    }
  }

  // highlight
  if(highlightZone !== null){
    const id = highlightZone - 1;
    const rr = Math.floor(id/{{ cols }});
    const cc = id % {{ cols }};
    ctx.save();
    ctx.strokeStyle = "yellow";
    ctx.lineWidth = 6;
    ctx.shadowColor = "yellow";
    ctx.shadowBlur = 15;
    ctx.strokeRect(cc*cellW+2, rr*cellH+2, cellW-4, cellH-4);
    ctx.restore();
  }
}

window.addEventListener("resize", redraw);

canvas.addEventListener("click", (e)=>{
  const rect = canvas.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;

  const cellW = rect.width / {{ cols }};
  const cellH = rect.height / {{ rows }};
  const col = Math.floor(x/cellW);
  const row = Math.floor(y/cellH);
  const zone = row*{{ cols }} + col; // 0-based
  document.getElementById("zoneSel").value = String(zone);
  openAdd();
});

function openAdd(){
  document.getElementById("addMsg").textContent="";
  document.getElementById("addModal").style.display="flex";
}
function closeModal(id){ document.getElementById(id).style.display="none"; }

function toggleManual(){
  addManual = !addManual;
  document.getElementById("cameraBlock").style.display = addManual ? "none" : "block";
  document.getElementById("manualBlock").style.display = addManual ? "block" : "none";
}

function toggleReady(){
  document.getElementById("readyBlock").style.display = document.getElementById("hasReady").checked ? "block" : "none";
}

async function submitAdd(){
  const zone = document.getElementById("zoneSel").value;
  if(zone===""){ alert("Bitte Zone wählen"); return; }

  const fd = new FormData();
  fd.append("zone", zone);

  if(addManual){
    fd.append("manual_vin", document.getElementById("vinIn").value);
    fd.append("manual_lager", document.getElementById("lagerIn").value);
  } else {
    const f = document.getElementById("imgFile").files[0];
    if(!f){ alert("Bitte Foto auswählen / aufnehmen"); return; }
    fd.append("image", f);
  }

  if(document.getElementById("hasReady").checked){
    fd.append("ready_date", document.getElementById("readyDate").value);
    fd.append("ready_time", document.getElementById("readyTime").value);
  }
  fd.append("notes", document.getElementById("notes").value);

  document.getElementById("addMsg").textContent="⏳ Speichern...";
  const res = await fetch("/api/add", {method:"POST", body: fd});
  const j = await res.json();
  if(!j.success){
    document.getElementById("addMsg").textContent = "❌ " + j.error;
    return;
  }
  location.reload();
}

async function removeCar(id){
  if(!confirm("Auto wirklich ausparken?")) return;
  const res = await fetch("/api/remove", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({car_id:id})
  });
  const j = await res.json();
  if(!j.success){ alert(j.error); return; }
  location.reload();
}

function openMove(id, currentZone){
  movingCarId = id;
  document.getElementById("moveZone").value = String(currentZone);
  document.getElementById("moveModal").style.display="flex";
}
async function doMove(){
  const target = document.getElementById("moveZone").value;
  const res = await fetch("/api/move", {
    method:"POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({car_id:movingCarId, target_zone: target})
  });
  const j = await res.json();
  if(!j.success){ alert(j.error); return; }
  location.reload();
}

async function doSearch(){
  const q = document.getElementById("q").value.trim();
  if(!q) return;
  const res = await fetch("/api/search?q="+encodeURIComponent(q));
  const j = await res.json();
  if(j && j.found && j.zone){
    highlightZone = j.zone;
    redraw();
    setTimeout(()=>{highlightZone=null; redraw();}, 8000);
  } else {
    alert("Nicht gefunden (auch nicht in History).");
  }
}

function exportHistoryRange(){
  const f = document.getElementById("from").value;
  const t = document.getElementById("to").value;
  if(!f || !t){ alert("Bitte Von und Bis wählen"); return; }
  window.location = `/api/export_history?from=${encodeURIComponent(f)}&to=${encodeURIComponent(t)}`;
}

// initial draw
setTimeout(redraw, 200);
</script>
</body>
</html>
"""

# ========= ROUTES =========
@app.route("/")
def index():
    total = sum(len(c) for c in zones.values())
    avail = MAX_CAPACITY - total

    fees_map = {}
    color_map = {}

    for z, cars in zones.items():
        for c in cars:
            fees = calculate_fees(c.get("ready_date"), c.get("in_time"), c.get("extras", []))
            fees_map[c["id"]] = fees
            color_map[c["id"]] = status_color(fees, c.get("ready_date"))

    return render_template_string(
        HTML,
        zones=zones,
        total=total,
        avail=avail,
        maxcap=MAX_CAPACITY,
        areas=AREAS,
        rows=ROWS,
        cols=COLS,
        fees_map=fees_map,
        color_map=color_map
    )

@app.route("/api/add", methods=["POST"])
def api_add():
    zone = request.form.get("zone", "")
    if zone == "":
        return jsonify(success=False, error="Keine Zone"), 400
    if zone not in zones:
        zones[zone] = []

    ready_date = request.form.get("ready_date") or None
    ready_time = request.form.get("ready_time") or None
    notes = request.form.get("notes") or ""

    vin = None
    lager = None

    manual_vin = request.form.get("manual_vin")
    manual_lager = request.form.get("manual_lager")

    # 1) Manual
    if manual_vin or manual_lager:
        vin = validate_vin(manual_vin)
        lager = validate_lager(manual_lager)
        if not vin:
            return jsonify(success=False, error="VIN ungültig (17 Zeichen)"), 400
        if not lager:
            return jsonify(success=False, error="Lagernummer ungültig (LK12345)"), 400

    # 2) OCR photo
    else:
        f = request.files.get("image")
        if not f:
            return jsonify(success=False, error="Kein Bild erhalten"), 400
        try:
            img = Image.open(f.stream)
            img = preprocess_image(img)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=90)
            buf.seek(0)

            r = requests.post(
                OCR_API_URL,
                files={"file": ("scan.jpg", buf, "image/jpeg")},
                data={"apikey": OCR_API_KEY, "language": "eng", "OCREngine": 2},
                timeout=35
            )
            data = r.json()
            text = ""
            if data.get("ParsedResults"):
                text = data["ParsedResults"][0].get("ParsedText", "") or ""
            text = text.upper()

            vm = VIN_RE.search(text)
            lm = LAGER_RE.search(text)

            vin = validate_vin(vm.group(0)) if vm else None
            lager = validate_lager(lm.group(0)) if lm else None

            if not vin:
                return jsonify(success=False, error="OCR: VIN nicht gefunden. Bitte manuell eingeben."), 400
            if not lager:
                return jsonify(success=False, error="OCR: Lagernummer nicht gefunden. Bitte manuell eingeben."), 400

        except Exception as e:
            logger.error(f"OCR error: {e}")
            return jsonify(success=False, error=f"OCR Fehler: {str(e)}"), 500

    extras = parse_extras_from_notes(notes)

    car = {
        "id": uuid.uuid4().hex,
        "vin": vin,
        "lager": lager,
        "in_time": now_str(),
        "ready_date": ready_date,
        "ready_time": ready_time,
        "extras": extras,
        "notes": notes
    }

    zones[zone].append(car)

    history.append({
        "id": car["id"],
        "vin": vin,
        "lager": lager,
        "zone_in": int(zone) + 1,
        "in_time": car["in_time"],
        "zone_out": "",
        "out_time": "",
        "ready_date": ready_date or "",
        "ready_time": ready_time or "",
        "fees": None
    })

    save_json(DATA_FILE, zones)
    save_json(HISTORY_FILE, history)
    return jsonify(success=True)

@app.route("/api/remove", methods=["POST"])
def api_remove():
    data = request.get_json(force=True)
    car_id = data.get("car_id")
    if not car_id:
        return jsonify(success=False, error="Keine car_id"), 400

    z, car = find_car_by_id(car_id)
    if not car:
        return jsonify(success=False, error="Auto nicht gefunden"), 404

    out_time = now_str()
    fees = calculate_fees(car.get("ready_date"), car.get("in_time"), car.get("extras", []))

    # history update
    for h in reversed(history):
        if h.get("id") == car_id and h.get("out_time") == "":
            h["out_time"] = out_time
            h["zone_out"] = int(z) + 1
            h["fees"] = fees
            break

    zones[z].remove(car)
    save_json(DATA_FILE, zones)
    save_json(HISTORY_FILE, history)
    return jsonify(success=True)

@app.route("/api/move", methods=["POST"])
def api_move():
    data = request.get_json(force=True)
    car_id = data.get("car_id")
    target = data.get("target_zone")
    if car_id is None or target is None:
        return jsonify(success=False, error="Daten fehlen"), 400
    if target not in zones:
        zones[target] = []

    z, car = find_car_by_id(car_id)
    if not car:
        return jsonify(success=False, error="Auto nicht gefunden"), 404

    zones[z].remove(car)
    zones[target].append(car)
    save_json(DATA_FILE, zones)
    return jsonify(success=True)

@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").upper().strip()
    if not q:
        return jsonify(found=False)

    # current
    for z, cars in zones.items():
        for c in cars:
            if q in c.get("vin","").upper() or q in c.get("lager","").upper():
                return jsonify(found=True, zone=int(z)+1, type="current", car=c)

    # history
    for h in reversed(history):
        if q in (h.get("vin","").upper()) or q in (h.get("lager","").upper()):
            return jsonify(found=True, zone=h.get("zone_in") or 1, type="history", car=h)

    return jsonify(found=False)

def csv_response(filename, content):
    resp = make_response(content)
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    return resp

@app.route("/api/export_all")
def export_all():
    out = StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["VIN","Lagernummer","Zone","Einlagerung","Bereitstellung","Tage_überzogen","Grundgebühr","Tagesgebühr","Extras","Gesamt","Notizen"])
    for z, cars in zones.items():
        for c in cars:
            fees = calculate_fees(c.get("ready_date"), c.get("in_time"), c.get("extras", []))
            w.writerow([
                c.get("vin",""),
                c.get("lager",""),
                int(z)+1,
                c.get("in_time",""),
                (c.get("ready_date") or "") + (" " + (c.get("ready_time") or "")).strip(),
                fees.get("overdue_days",0),
                fees.get("base_fee",0),
                fees.get("daily_fee_total",0),
                fees.get("extras_total",0),
                fees.get("grand_total",0),
                c.get("notes","")
            ])
    return csv_response(f"parkplatz_alle_{datetime.now().strftime('%Y%m%d')}.csv", out.getvalue())

@app.route("/api/export_history")
def export_history():
    frm = request.args.get("from")  # YYYY-MM-DD
    to = request.args.get("to")
    if not frm or not to:
        return jsonify(success=False, error="from/to fehlen"), 400

    out = StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["VIN","Lagernummer","Zone_in","Einlagerung","Auslagerung","Zone_out","Bereitstellung","Tage_überzogen","Grundgebühr","Tagesgebühr","Extras","Gesamt"])
    for h in history:
        if not h.get("out_time"):
            continue
        # out_time startswith date
        out_date = h["out_time"][:10]
        if frm <= out_date <= to:
            fees = h.get("fees") or {}
            w.writerow([
                h.get("vin",""),
                h.get("lager",""),
                h.get("zone_in",""),
                h.get("in_time",""),
                h.get("out_time",""),
                h.get("zone_out",""),
                (h.get("ready_date","") + " " + h.get("ready_time","")).strip(),
                fees.get("overdue_days",0),
                fees.get("base_fee",0),
                fees.get("daily_fee_total",0),
                fees.get("extras_total",0),
                fees.get("grand_total",0),
            ])

    return csv_response(f"parkplatz_history_{frm}_bis_{to}.csv", out.getvalue())

# ========= START =========
if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    if not os.path.exists("static/parking_lot.png"):
        print("⚠️ FEHLT: static/parking_lot.png")
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
