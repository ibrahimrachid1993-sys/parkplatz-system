import os, json, re, uuid, csv
from datetime import datetime
from io import BytesIO

import requests
from PIL import Image, ImageEnhance

from flask import Flask, render_template_string, request, redirect, jsonify, send_file

app = Flask(__name__)

# ===== SETTINGS =====
ROWS = 4
COLS = 4
AREAS = ROWS * COLS
MAX_CAPACITY = 650
DATA_FILE = "data.json"

# OCR.Space Key (dein Key)
OCR_API_KEY = "K86896713788957"
OCR_API_URL = "https://api.ocr.space/parse/image"
# ====================

VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
LAGER_RE = re.compile(r"\b[A-Z]{2}\d{5}\b")

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "zones": {str(i): [] for i in range(AREAS)},
        "history": []
    }

def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

data = load_data()

def total_cars():
    return sum(len(data["zones"][z]) for z in data["zones"])

def preprocess_image(image: Image.Image) -> Image.Image:
    if image.mode != "L":
        image = image.convert("L")
    image = ImageEnhance.Contrast(image).enhance(2.0)
    image = ImageEnhance.Brightness(image).enhance(1.1)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    return image

def extract_vin_lager(text: str):
    text = (text or "").upper()
    vin = VIN_RE.search(text)
    lager = LAGER_RE.search(text)
    return (vin.group(0) if vin else ""), (lager.group(0) if lager else "")

def find_car(query: str):
    q = (query or "").upper().strip()
    if not q:
        return None
    # search in active zones
    for z, cars in data["zones"].items():
        for c in cars:
            if q in (c.get("vin","").upper()) or q in (c.get("lager","").upper()):
                return {"where":"active","zone":int(z)+1,"car":c}
    # search in history
    for c in reversed(data["history"]):
        if q in (c.get("vin","").upper()) or q in (c.get("lager","").upper()):
            return {"where":"history","zone":c.get("zone","—"),"car":c}
    return None

# ===== ROUTES =====
@app.route("/")
def index():
    return render_template_string(
        PAGE,
        rows=ROWS, cols=COLS,
        zones=data["zones"],
        history=list(reversed(data["history"]))[:200],
        total=total_cars(),
        max=MAX_CAPACITY,
        avail=MAX_CAPACITY - total_cars()
    )

@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    # accepts: multipart/form-data file=image
    if "image" not in request.files:
        return jsonify({"ok": False, "error": "Kein Bild gesendet"}), 400

    file = request.files["image"]
    try:
        img = Image.open(file.stream)
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
        j = r.json()
        parsed_text = ""
        if j.get("ParsedResults"):
            parsed_text = j["ParsedResults"][0].get("ParsedText","")

        vin, lager = extract_vin_lager(parsed_text)
        return jsonify({"ok": True, "vin": vin, "lager": lager, "raw": parsed_text[:800]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/add", methods=["POST"])
def api_add():
    zone = request.form.get("zone","")
    vin = (request.form.get("vin","") or "").upper().strip()
    lager = (request.form.get("lager","") or "").upper().strip()

    if zone == "" or zone not in data["zones"]:
        return jsonify({"ok": False, "error":"Zone ungültig"}), 400

    if not VIN_RE.fullmatch(vin):
        return jsonify({"ok": False, "error":"VIN ungültig (muss 17 Zeichen sein)"}), 400

    if not LAGER_RE.fullmatch(lager):
        return jsonify({"ok": False, "error":"Lagernummer ungültig (2 Buchstaben + 5 Ziffern)"}), 400

    car = {
        "id": uuid.uuid4().hex,
        "vin": vin,
        "lager": lager,
        "in_time": now_str()
    }
    data["zones"][zone].append(car)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/remove", methods=["POST"])
def api_remove():
    car_id = request.form.get("car_id","")
    if not car_id:
        return jsonify({"ok": False, "error":"Keine ID"}), 400

    for z, cars in data["zones"].items():
        for c in cars:
            if c["id"] == car_id:
                out_time = now_str()
                history_item = {
                    "id": c["id"],
                    "vin": c["vin"],
                    "lager": c["lager"],
                    "in_time": c["in_time"],
                    "out_time": out_time,
                    "zone": int(z)+1
                }
                data["history"].append(history_item)
                cars.remove(c)
                save_data(data)
                return jsonify({"ok": True})

    return jsonify({"ok": False, "error":"Auto nicht gefunden"}), 404

@app.route("/api/move", methods=["POST"])
def api_move():
    car_id = request.form.get("car_id","")
    to_zone = request.form.get("to_zone","")
    if to_zone not in data["zones"]:
        return jsonify({"ok": False, "error":"Ziel-Zone ungültig"}), 400

    for z, cars in data["zones"].items():
        for c in cars:
            if c["id"] == car_id:
                cars.remove(c)
                data["zones"][to_zone].append(c)
                save_data(data)
                return jsonify({"ok": True})

    return jsonify({"ok": False, "error":"Auto nicht gefunden"}), 404

@app.route("/export_all")
def export_all():
    fn = "export_all.csv"
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Status","Zone","VIN","Lagernummer","Einlagerung","Auslagerung"])
        for z, cars in data["zones"].items():
            for c in cars:
                w.writerow(["AKTIV", int(z)+1, c["vin"], c["lager"], c["in_time"], ""])
        for h in data["history"]:
            w.writerow(["HISTORY", h.get("zone",""), h["vin"], h["lager"], h["in_time"], h["out_time"]])
    return send_file(fn, as_attachment=True)

@app.route("/export_history_range", methods=["POST"])
def export_history_range():
    start = request.form.get("start","")
    end = request.form.get("end","")
    fn = "export_history_range.csv"
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Zone","VIN","Lagernummer","Einlagerung","Auslagerung"])
        for h in data["history"]:
            d = (h.get("out_time","")[:10] or "")
            if d and start <= d <= end:
                w.writerow([h.get("zone",""), h["vin"], h["lager"], h["in_time"], h["out_time"]])
    return send_file(fn, as_attachment=True)

# ===== UI =====
PAGE = r"""
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Parkplatz</title>
<style>
:root{
  --bg:#f5f7fb;
  --card:#ffffff;
  --text:#0f172a;
  --muted:#64748b;
  --pri:#2563eb;
  --ok:#22c55e;
  --danger:#ef4444;
  --shadow:0 10px 25px rgba(2,6,23,.10);
  --r:16px;
}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--text)}
.wrap{max-width:980px;margin:0 auto;padding:14px 14px 30px}
.header{
  display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;
  background:linear-gradient(135deg,#dbeafe,#efe7ff);
  border:1px solid rgba(15,23,42,.10);
  padding:14px;border-radius:calc(var(--r) + 6px);box-shadow:var(--shadow);
}
.header b{font-size:16px}
.pills{display:flex;gap:8px;flex-wrap:wrap}
.pill{background:#fff;border:1px solid rgba(15,23,42,.10);padding:8px 10px;border-radius:999px;font-size:12px;color:var(--muted)}
.pill b{color:var(--text)}
.grid{display:grid;grid-template-columns:1fr;gap:12px;margin-top:12px}
@media(min-width:860px){.grid{grid-template-columns:1.25fr .75fr}}
.card{background:var(--card);border:1px solid rgba(15,23,42,.08);border-radius:var(--r);box-shadow:var(--shadow);overflow:hidden}
.card .hd{padding:12px 14px;display:flex;justify-content:space-between;align-items:center;gap:10px}
.card .hd h3{margin:0;font-size:14px}
.card .bd{padding:0 14px 14px}
canvas{width:100%;display:block;border-radius:calc(var(--r) + 8px)}
.canvasWrap{padding:10px}
.btn{border:0;border-radius:14px;padding:10px 12px;font-weight:800;background:var(--pri);color:#fff;width:100%}
.btn.secondary{background:#fff;color:var(--text);border:1px solid rgba(15,23,42,.12)}
.btn.danger{background:var(--danger)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
input,select{width:100%;padding:12px;border-radius:14px;border:1px solid rgba(15,23,42,.12);font-size:14px;background:#fff}
.small{font-size:12px;color:var(--muted)}
details summary{cursor:pointer;font-weight:900;padding:12px 14px;list-style:none}
details summary::-webkit-details-marker{display:none}
.zoneBox{padding:0 14px 14px;border-top:1px solid rgba(15,23,42,.08)}
.car{background:#fff;border:1px solid rgba(15,23,42,.08);border-radius:14px;padding:12px;margin-top:10px}
.carTop{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}
.badge{display:inline-block;padding:6px 10px;border-radius:999px;background:rgba(37,99,235,.12);color:#1d4ed8;font-weight:900;font-size:12px}
.modal{position:fixed;inset:0;background:rgba(2,6,23,.45);display:none;align-items:center;justify-content:center;padding:14px;z-index:50}
.modal .box{background:#fff;border-radius:20px;max-width:520px;width:100%;box-shadow:0 20px 50px rgba(0,0,0,.25);overflow:hidden}
.modal .box .hd{padding:14px;border-bottom:1px solid rgba(15,23,42,.08);display:flex;justify-content:space-between;align-items:center}
.x{background:transparent;border:0;font-size:26px;cursor:pointer}
.flash{padding:10px 14px;color:#065f46;background:#dcfce7;border-top:1px solid rgba(0,0,0,.06);display:none}
.err{padding:10px 14px;color:#7f1d1d;background:#fee2e2;border-top:1px solid rgba(0,0,0,.06);display:none}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div>
      <b>🅿️ Parkplatz</b><div class="small">Tippe auf eine Zone im Bild → hinzufügen. Suche markiert Zone grün.</div>
    </div>
    <div class="pills">
      <div class="pill">Belegt: <b>{{total}}</b></div>
      <div class="pill">Frei: <b>{{avail}}</b></div>
      <div class="pill">Kapazität: <b>{{max}}</b></div>
    </div>
  </div>

  <div class="grid">

    <div class="card">
      <div class="hd">
        <h3>🗺️ Karte</h3>
        <span class="badge">Zonen 1–16</span>
      </div>
      <div class="canvasWrap">
        <canvas id="map"></canvas>
      </div>
      <div class="bd">
        <div class="row2">
          <input id="q" placeholder="Suche VIN oder Lagernummer…" />
          <button class="btn secondary" type="button" onclick="doSearch()">🔍 Suchen</button>
        </div>
        <div class="small" id="searchInfo" style="margin-top:8px"></div>
      </div>
    </div>

    <div class="card">
      <div class="hd"><h3>📤 CSV Export</h3><span class="small">Excel-fertig</span></div>
      <div class="bd">
        <a href="/export_all" style="text-decoration:none"><button class="btn" type="button">Alles exportieren</button></a>
        <div style="height:10px"></div>
        <form action="/export_history_range" method="post">
          <div class="row2">
            <div>
              <div class="small">Von (Auslagerung)</div>
              <input type="date" name="start" required>
            </div>
            <div>
              <div class="small">Bis (Auslagerung)</div>
              <input type="date" name="end" required>
            </div>
          </div>
          <button class="btn secondary" style="margin-top:10px" type="submit">History Zeitraum exportieren</button>
        </form>
      </div>
    </div>

  </div>

  <div class="card" style="margin-top:12px">
    <div class="hd"><h3>🚗 Zonen</h3><span class="small">Einklappbar</span></div>

    {% for z in zones %}
    <details {% if loop.index==1 %}open{% endif %}>
      <summary>Zone {{ z|int + 1 }} <span class="small">({{ zones[z]|length }} Fahrzeuge)</span></summary>
      <div class="zoneBox">
        {% for c in zones[z] %}
          <div class="car">
            <div class="carTop">
              <div>
                <div style="font-weight:1000;letter-spacing:.2px">{{c.vin}}</div>
                <div class="small">Lager: <b style="color:var(--text)">{{c.lager}}</b></div>
                <div class="small">Einlagerung: {{c.in_time}}</div>
              </div>

              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <form onsubmit="return moveCar(event)" style="display:flex;gap:8px;align-items:center">
                  <input type="hidden" name="car_id" value="{{c.id}}">
                  <select name="to_zone">
                    {% for i in range(rows*cols) %}
                      <option value="{{i}}" {% if i==z|int %}selected{% endif %}>Zone {{i+1}}</option>
                    {% endfor %}
                  </select>
                  <button class="btn secondary" type="submit">↔️ Move</button>
                </form>

                <form onsubmit="return removeCar(event)">
                  <input type="hidden" name="car_id" value="{{c.id}}">
                  <button class="btn danger" type="submit">🚚 Auslagern</button>
                </form>
              </div>
            </div>
          </div>
        {% else %}
          <div class="small">Noch keine Fahrzeuge.</div>
        {% endfor %}
      </div>
    </details>
    {% endfor %}
  </div>

  <div class="card">
    <div class="hd"><h3>🧾 Verlauf</h3><span class="small">{{history|length}} angezeigt (max 200)</span></div>
    <div class="bd">
      {% if history %}
        {% for c in history %}
          <div class="car">
            <div style="font-weight:1000">{{c.vin}}</div>
            <div class="small">Lager: <b style="color:var(--text)">{{c.lager}}</b></div>
            <div class="small">Zone: {{c.zone}}</div>
            <div class="small">Rein: {{c.in_time}} | Raus: {{c.out_time}}</div>
          </div>
        {% endfor %}
      {% else %}
        <div class="small">Noch nichts ausgelagert.</div>
      {% endif %}
    </div>
  </div>

</div>

<!-- MODAL: Add Car -->
<div class="modal" id="modal">
  <div class="box">
    <div class="hd">
      <div>
        <div style="font-weight:1000">➕ Auto einlagern</div>
        <div class="small">Zone: <span id="mz"></span></div>
      </div>
      <button class="x" onclick="closeModal()">×</button>
    </div>

    <div class="flash" id="flash"></div>
    <div class="err" id="err"></div>

    <div class="bd" style="padding:14px">
      <form id="addForm" onsubmit="return addCar(event)">
        <input type="hidden" name="zone" id="zone">

        <div class="small" style="margin:6px 0">VIN (17 Zeichen)</div>
        <input name="vin" id="vin" placeholder="z.B. W0L0AHL08G1234567" required>

        <div class="small" style="margin:10px 0 6px">Lagernummer (2 Buchstaben + 5 Ziffern)</div>
        <input name="lager" id="lager" placeholder="z.B. LK12345" required>

        <div class="small" style="margin:10px 0 6px">📸 Scan (optional – füllt VIN/Lager automatisch)</div>
        <input type="file" id="scan" accept="image/*" capture="environment" onchange="scanOCR(this)">

        <button class="btn" style="margin-top:12px" type="submit">✅ Speichern</button>
      </form>
    </div>
  </div>
</div>

<script>
const rows={{rows}}, cols={{cols}};
const zones={{zones|tojson}};
let highlight = null;

const canvas=document.getElementById("map");
const ctx=canvas.getContext("2d");
const img=new Image();
img.src="/static/parking_lot.png";

img.onload=()=>draw();

function draw(){
  canvas.width = img.width;
  canvas.height = img.height;
  ctx.drawImage(img,0,0);

  const w=canvas.width/cols, h=canvas.height/rows;

  for(let r=0;r<rows;r++){
    for(let c=0;c<cols;c++){
      const i=r*cols+c;
      const count=(zones[String(i)]||[]).length;

      // overlay if has cars
      if(count>0){
        ctx.fillStyle="rgba(37,99,235,.22)";
        ctx.fillRect(c*w,r*h,w,h);
      }

      // highlight zone (search)
      if(highlight===i){
        ctx.fillStyle="rgba(34,197,94,.35)";
        ctx.fillRect(c*w,r*h,w,h);
        ctx.lineWidth=8;
        ctx.strokeStyle="rgba(34,197,94,.95)";
        ctx.strokeRect(c*w+3,r*h+3,w-6,h-6);
      }

      // border
      ctx.lineWidth=4;
      ctx.strokeStyle="rgba(255,255,255,.85)";
      ctx.strokeRect(c*w,r*h,w,h);
      ctx.lineWidth=2;
      ctx.strokeStyle="rgba(15,23,42,.65)";
      ctx.strokeRect(c*w,r*h,w,h);

      // number
      ctx.fillStyle="rgba(15,23,42,.88)";
      ctx.font="bold 18px Arial";
      ctx.fillText(String(i+1), c*w+12, r*h+24);

      // count badge
      if(count>0){
        ctx.fillStyle="rgba(15,23,42,.70)";
        ctx.fillRect(c*w+10, r*h+h-40, 44, 28);
        ctx.fillStyle="#fff";
        ctx.font="bold 16px Arial";
        ctx.fillText(String(count), c*w+26, r*h+h-20);
      }
    }
  }
}

canvas.addEventListener("click",(e)=>{
  const rect=canvas.getBoundingClientRect();
  const x=(e.clientX-rect.left)*(canvas.width/rect.width);
  const y=(e.clientY-rect.top)*(canvas.height/rect.height);
  const col=Math.floor(x/(canvas.width/cols));
  const row=Math.floor(y/(canvas.height/rows));
  const zone=row*cols+col;
  openModal(zone);
});

function openModal(zone){
  document.getElementById("modal").style.display="flex";
  document.getElementById("zone").value=String(zone);
  document.getElementById("mz").innerText=String(zone+1);
  document.getElementById("vin").value="";
  document.getElementById("lager").value="";
  setMsg("flash","");
  setMsg("err","");
}
function closeModal(){
  document.getElementById("modal").style.display="none";
}

function setMsg(id, txt){
  const el=document.getElementById(id);
  if(!txt){ el.style.display="none"; el.innerText=""; return; }
  el.style.display="block";
  el.innerText=txt;
}

async function scanOCR(input){
  const file=input.files && input.files[0];
  if(!file) return;

  setMsg("flash","⏳ Scanne…");
  setMsg("err","");

  const fd=new FormData();
  fd.append("image", file);

  try{
    const r=await fetch("/api/ocr",{method:"POST",body:fd});
    const j=await r.json();
    if(!j.ok){
      setMsg("flash","");
      setMsg("err","Scan Fehler: "+(j.error||""));
      return;
    }
    if(j.vin) document.getElementById("vin").value=j.vin;
    if(j.lager) document.getElementById("lager").value=j.lager;

    if(!j.vin && !j.lager){
      setMsg("flash","");
      setMsg("err","Nichts erkannt. Mach ein schärferes Foto (näher, mehr Licht).");
      return;
    }
    setMsg("flash","✅ Scan ok – Felder gefüllt.");
  }catch(e){
    setMsg("flash","");
    setMsg("err","Netzwerkfehler beim Scan.");
  }
}

async function addCar(e){
  e.preventDefault();
  setMsg("flash","⏳ Speichere…");
  setMsg("err","");

  const fd=new FormData(document.getElementById("addForm"));
  const r=await fetch("/api/add",{method:"POST",body:fd});
  const j=await r.json();
  if(!j.ok){
    setMsg("flash","");
    setMsg("err", j.error || "Fehler");
    return false;
  }
  location.reload();
  return false;
}

async function removeCar(e){
  e.preventDefault();
  const fd=new FormData(e.target);
  const r=await fetch("/api/remove",{method:"POST",body:fd});
  const j=await r.json();
  if(!j.ok){ alert(j.error||"Fehler"); return false; }
  location.reload();
  return false;
}

async function moveCar(e){
  e.preventDefault();
  const fd=new FormData(e.target);
  const r=await fetch("/api/move",{method:"POST",body:fd});
  const j=await r.json();
  if(!j.ok){ alert(j.error||"Fehler"); return false; }
  location.reload();
  return false;
}

function doSearch(){
  const q=(document.getElementById("q").value||"").toUpperCase().trim();
  const info=document.getElementById("searchInfo");
  if(!q){ highlight=null; info.innerText=""; draw(); return; }

  let found=null;
  for(const z in zones){
    for(const c of zones[z]){
      if((c.vin||"").toUpperCase().includes(q) || (c.lager||"").toUpperCase().includes(q)){
        found=parseInt(z,10);
        break;
      }
    }
    if(found!==null) break;
  }

  if(found===null){
    highlight=null;
    info.innerText="❌ Nicht gefunden (evtl. im Verlauf).";
  }else{
    highlight=found;
    info.innerText="✅ Gefunden in Zone "+String(found+1)+" (grün markiert)";
    setTimeout(()=>{ highlight=null; draw(); info.innerText=""; }, 12000);
  }
  draw();
}
</script>

</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
